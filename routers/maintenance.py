"""
Router: maintenance
===================
Admin-only database maintenance endpoints.

Prefix  : /api/admin/maintenance   (registered in main.py)
Tags    : ["Admin Maintenance"]
Auth    : Two-layer guard (JWT + optional X-Admin-Key) — identical pattern
          to routers/analytics_admin.py.

Endpoints
---------
DELETE /cleanup-recipes   Purge stale temporary records older than N days,
                          preserving every recipe the user explicitly saved
                          as a Favourite.

Design rules
------------
- Favourites are NEVER deleted — the cross-check is done BEFORE any DELETE
  so a crash mid-cleanup cannot leave the DB in an inconsistent state.
- The endpoint supports ?dry_run=true to preview what would be deleted
  without committing any changes.
- Three tables are cleaned in one transaction:
    1. recipe_cache     — DB fallback cache (stale entries)
    2. password_reset_tokens — tokens past their expires_at timestamp
    3. usage_logs       — old API-cost tracking rows (30-day retention)
- recipe_cache rows whose JSON payload contains a title that matches ANY
  row in the `favorites` table are skipped and counted as "protected".
- All deletes use synchronize_session=False for performance; the session
  is not used after the bulk deletes.
- One db.commit() at the very end — no partial commits.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import Favorite, User

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Admin key (mirrors analytics_admin.py — same env var) ────────────────────
_ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

# Retention windows
_CACHE_TTL_DAYS: int = 7
_USAGE_LOG_TTL_DAYS: int = 30


# ============================================================================
# ADMIN DEPENDENCY  (duplicated from analytics_admin to avoid cross-router
#                    imports; behaviour is identical)
# ============================================================================

def require_admin(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Two-layer admin guard.
    1. JWT is always required (get_current_user handles it).
    2. When ADMIN_API_KEY is set in env, the X-Admin-Key header must match.
       Unset → any authenticated user passes (development convenience).
    """
    if _ADMIN_API_KEY and x_admin_key != _ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso de administrador necessário. Fornece o header X-Admin-Key.",
        )
    return current_user


# ============================================================================
# HELPERS
# ============================================================================

def _extract_titles_from_cache_json(response_json: str) -> set[str]:
    """
    Parse a RecipeCache.response_json payload and return the set of recipe
    titles it contains.

    The payload is either:
      - a JSON list of recipe dicts  →  [{"title": "...", ...}, ...]
      - a JSON dict with a "recipes" key  →  {"recipes": [...]}

    Returns an empty set on any parse error — callers treat that as
    "no recognised titles, safe to delete".
    """
    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, TypeError):
        return set()

    if isinstance(data, list):
        recipes = data
    elif isinstance(data, dict):
        recipes = data.get("recipes", [])
    else:
        return set()

    return {
        r["title"]
        for r in recipes
        if isinstance(r, dict) and r.get("title")
    }


# ============================================================================
# ENDPOINT
# ============================================================================

@router.delete(
    "/cleanup-recipes",
    summary="Limpeza de registos temporários da base de dados",
    response_description="Contagem de registos eliminados e detalhes por tabela",
)
def cleanup_old_records(
    days: int = Query(
        default=_CACHE_TTL_DAYS,
        ge=1,
        le=365,
        description=(
            f"Eliminar registos de cache mais antigos que N dias "
            f"(padrão: {_CACHE_TTL_DAYS}). "
            "Tokens expirados e usage_logs > 30 dias são sempre limpos."
        ),
    ),
    dry_run: bool = Query(
        default=False,
        description=(
            "Se true, calcula o que seria eliminado sem apagar nada. "
            "Útil para verificar o impacto antes de executar a limpeza real."
        ),
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Purga registos temporários e órfãos da base de dados, preservando
    **sempre** as receitas que os utilizadores guardaram explicitamente
    como favoritas.

    Tabelas limpas
    --------------
    **recipe_cache** — entradas de cache do DB (fallback ao Redis) criadas
      há mais de `days` dias. Antes de apagar cada entrada, o JSON da coluna
      `response_json` é parsed e os títulos das receitas são comparados com
      a tabela `favorites`. Qualquer entrada cujo payload contenha uma receita
      favorita é **protegida** e contada em `recipe_cache_protected`.

    **password_reset_tokens** — tokens cuja `expires_at` já passou.
      Nunca há risco de apagar um token válido.

    **usage_logs** — registos de custo/tokens com mais de 30 dias.
      Dados de faturação e analytics são preservados em tabelas separadas.

    Parâmetros
    ----------
    days      Janela de retenção para recipe_cache (mín 1, máx 365).
    dry_run   Se true, devolve os contadores sem executar nenhum DELETE.

    Resposta
    --------
    ```json
    {
      "status": "success",
      "deleted_records_count": 42,
      "details": {
        "recipe_cache_deleted": 30,
        "recipe_cache_protected": 5,
        "expired_tokens_deleted": 7,
        "usage_logs_deleted": 5,
        "cutoff_date": "2026-06-25T20:17:00",
        "usage_log_cutoff_date": "2026-06-02T20:17:00"
      },
      "message": "Limpeza de manutenção concluída com sucesso."
    }
    ```
    """

    # ── Lazy import to break the main.py → routers → main.py cycle ───────────
    # RecipeCache, PasswordResetToken, UsageLog are SQLAlchemy models defined
    # inline in main.py (pre-existing architecture). Importing them at module
    # level here would create a circular import. The lazy import inside the
    # function body runs AFTER both modules are fully loaded, which is safe.
    from main import PasswordResetToken, RecipeCache, UsageLog  # noqa: PLC0415

    now = datetime.utcnow()
    cache_cutoff     = now - timedelta(days=days)
    usage_log_cutoff = now - timedelta(days=_USAGE_LOG_TTL_DAYS)

    # ── Step 1: Load all favorited recipe titles (the protected set) ──────────
    # Using a set gives O(1) membership tests in the inner loop below.
    # This query is cheap — titles are short strings, not BLOBs.
    favorited_titles: set[str] = {
        row[0]
        for row in db.query(Favorite.recipe_title).all()
        if row[0]
    }

    logger.info(
        "[maintenance] cleanup_old_records started | "
        "days=%d dry_run=%s cutoff=%s favorited_titles=%d by_admin=%d",
        days, dry_run, cache_cutoff.isoformat(), len(favorited_titles), admin.id,
    )

    # ── Step 2: Identify stale recipe_cache entries ───────────────────────────
    stale_cache_rows = (
        db.query(RecipeCache)
        .filter(RecipeCache.created_at < cache_cutoff)
        .all()
    )

    cache_to_delete:  list[RecipeCache] = []
    protected_count:  int = 0

    for row in stale_cache_rows:
        titles_in_payload = _extract_titles_from_cache_json(row.response_json or "")

        # If ANY recipe in this cache entry has been favorited, keep the entry.
        if titles_in_payload & favorited_titles:
            protected_count += 1
            logger.debug(
                "[maintenance] Skipping cache id=%d — contains favorited recipe(s): %s",
                row.id,
                titles_in_payload & favorited_titles,
            )
        else:
            cache_to_delete.append(row)

    # ── Step 3: Identify expired password reset tokens ────────────────────────
    # Any token whose expires_at has passed is unredeemable and safe to delete.
    expired_tokens = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.expires_at < now)
        .all()
    )

    # ── Step 4: Identify old usage logs ───────────────────────────────────────
    old_usage_logs = (
        db.query(UsageLog)
        .filter(UsageLog.created_at < usage_log_cutoff)
        .all()
    )

    # ── Totals ────────────────────────────────────────────────────────────────
    n_cache  = len(cache_to_delete)
    n_tokens = len(expired_tokens)
    n_logs   = len(old_usage_logs)
    total    = n_cache + n_tokens + n_logs

    details = {
        "recipe_cache_deleted":   n_cache  if not dry_run else 0,
        "recipe_cache_protected": protected_count,
        "expired_tokens_deleted": n_tokens if not dry_run else 0,
        "usage_logs_deleted":     n_logs   if not dry_run else 0,
        "cutoff_date":            cache_cutoff.isoformat(),
        "usage_log_cutoff_date":  usage_log_cutoff.isoformat(),
    }

    if dry_run:
        logger.info(
            "[maintenance] DRY RUN — would delete %d record(s): "
            "cache=%d tokens=%d logs=%d protected=%d",
            total, n_cache, n_tokens, n_logs, protected_count,
        )
        return {
            "status":                "dry_run",
            "deleted_records_count": 0,
            "would_delete_count":    total,
            "details":               {**details,
                                      "recipe_cache_deleted": n_cache,
                                      "expired_tokens_deleted": n_tokens,
                                      "usage_logs_deleted": n_logs},
            "message": (
                f"Simulação concluída. {total} registo(s) seriam eliminados. "
                "Execute sem dry_run=true para aplicar a limpeza."
            ),
        }

    # ── Step 5: Execute deletes in one transaction ────────────────────────────
    # All deletes happen before the single commit so the DB is never left
    # in a partially cleaned state if an exception occurs mid-loop.
    for row in cache_to_delete:
        db.delete(row)

    for token in expired_tokens:
        db.delete(token)

    for log in old_usage_logs:
        db.delete(log)

    db.commit()

    logger.info(
        "[maintenance] Cleanup committed: deleted %d record(s) "
        "(cache=%d tokens=%d logs=%d protected=%d)",
        total, n_cache, n_tokens, n_logs, protected_count,
    )

    return {
        "status":                "success",
        "deleted_records_count": total,
        "details":               details,
        "message":               "Limpeza de manutenção concluída com sucesso.",
    }
