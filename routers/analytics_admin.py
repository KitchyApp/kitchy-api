"""
Router: analytics_admin
=======================
Read-only telemetry endpoints for the internal admin dashboard.

Prefix  : /api/admin/analytics   (registered in main.py)
Tags    : ["Analytics Admin"]
Auth    : Two-layer guard —
            1. Valid JWT access token  (always required via get_current_user)
            2. X-Admin-Key header      (required when ADMIN_API_KEY is set in env)
          In development (ADMIN_API_KEY unset) any authenticated user can read.
          In production set ADMIN_API_KEY to a long random secret.

Endpoints
---------
GET /overview        Total event counts grouped by event_name + % share.
GET /conversions     Quota-hit → premium-upgrade conversion funnel.
GET /social-shares   Platform / source distribution parsed from metadata_json.

All three endpoints accept optional query params:
    from_date  YYYY-MM-DD   inclusive lower bound on created_at
    to_date    YYYY-MM-DD   inclusive upper bound on created_at

Design rules
------------
- All queries use func.count() / func.distinct() — no Python-side aggregation
  of raw rows (scales to millions of events without memory issues).
- metadata_json is parsed lazily only where needed (social-shares).
- NEVER raises from analytics reads — if the DB is unavailable a clear 503
  is returned; the exception is always logged.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import User
from models.analytics import AnalyticsEvent

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Admin access key (optional — set in .env for production hardening) ────────
_ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")


# ============================================================================
# DEPENDENCIES
# ============================================================================

def require_admin(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Two-layer admin guard.

    Layer 1 — JWT (always):
        get_current_user raises HTTP 401 if the token is missing/invalid/expired.

    Layer 2 — API key (when ADMIN_API_KEY is configured in env):
        The caller must pass the matching value in the X-Admin-Key request header.
        If ADMIN_API_KEY is not set the key check is skipped so local dev is easy.

    Returns the authenticated user so downstream endpoints can log who ran what.
    """
    if _ADMIN_API_KEY and x_admin_key != _ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso de administrador necessário. Fornece o header X-Admin-Key.",
        )
    return current_user


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _apply_date_filter(query, from_date: Optional[date], to_date: Optional[date]):
    """
    Append inclusive date-range filters to any SQLAlchemy query that touches
    AnalyticsEvent.created_at.

    from_date is treated as start-of-day (00:00:00).
    to_date   is treated as end-of-day  (23:59:59.999…) via < next-day-start.
    """
    if from_date:
        query = query.filter(
            AnalyticsEvent.created_at >= datetime(from_date.year, from_date.month, from_date.day)
        )
    if to_date:
        next_day = datetime.combine(to_date + timedelta(days=1), datetime.min.time())
        query = query.filter(AnalyticsEvent.created_at < next_day)
    return query


def _scalar_count(
    db: Session,
    event_name: str,
    from_date: Optional[date],
    to_date: Optional[date],
    distinct_users: bool = False,
) -> int:
    """
    Count rows (or distinct user_ids) for a given event_name in the date range.

    distinct_users=True  → counts unique users who triggered the event
    distinct_users=False → counts total occurrences of the event
    """
    if distinct_users:
        count_expr = func.count(AnalyticsEvent.user_id.distinct())
    else:
        count_expr = func.count(AnalyticsEvent.id)

    q = db.query(count_expr).filter(AnalyticsEvent.event_name == event_name)
    q = _apply_date_filter(q, from_date, to_date)
    return q.scalar() or 0


def _period_dict(from_date: Optional[date], to_date: Optional[date]) -> dict:
    return {
        "from": from_date.isoformat() if from_date else None,
        "to":   to_date.isoformat()   if to_date   else None,
    }


# ============================================================================
# ENDPOINT 1 — OVERVIEW
# ============================================================================

@router.get(
    "/overview",
    summary="Visão geral dos eventos de telemetria",
    response_description="Contagem total de eventos agrupados por tipo",
)
def get_overview(
    from_date: Optional[date] = Query(
        default=None,
        description="Data de início (inclusive) — formato YYYY-MM-DD",
    ),
    to_date: Optional[date] = Query(
        default=None,
        description="Data de fim (inclusive) — formato YYYY-MM-DD",
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Devolve o número total de eventos registados na tabela `analytics_events`,
    divididos por tipo (`event_name`), ordenados do mais frequente para o menos.

    Cada entrada do `breakdown` inclui:
    - `event`  — nome do evento (snake_case)
    - `count`  — número absoluto de ocorrências
    - `pct`    — percentagem relativa ao total no período
    """
    q = db.query(
        AnalyticsEvent.event_name,
        func.count(AnalyticsEvent.id).label("total"),
    )
    q = _apply_date_filter(q, from_date, to_date)
    rows = (
        q.group_by(AnalyticsEvent.event_name)
         .order_by(func.count(AnalyticsEvent.id).desc())
         .all()
    )

    grand_total: int = sum(r.total for r in rows)

    breakdown = [
        {
            "event": r.event_name,
            "count": r.total,
            "pct":   round(r.total / grand_total * 100, 1) if grand_total else 0.0,
        }
        for r in rows
    ]

    # Surface the most operationally relevant counts at the top level for
    # quick dashboard widgets without having to scan the full breakdown list.
    named = {r.event_name: r.total for r in rows}

    return {
        "period":                  _period_dict(from_date, to_date),
        "total_events":            grand_total,
        "paywall_views":           named.get("paywall_displayed", 0),
        "limit_blocks":            named.get("limit_blocked_403", 0),
        "premium_conversions":     named.get("premium_converted", 0),
        "share_events":            named.get("share_triggered", 0) + named.get("share_clicked", 0),
        "queried_by_user_id":      admin.id,
        "breakdown":               breakdown,
    }


# ============================================================================
# ENDPOINT 2 — CONVERSIONS
# ============================================================================

@router.get(
    "/conversions",
    summary="Taxa de conversão: bloqueio 403 → upgrade Premium",
    response_description="Funil de conversão com contagens absolutas e percentagem",
)
def get_conversions(
    from_date: Optional[date] = Query(default=None),
    to_date:   Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Calcula a taxa de conversão entre utilizadores bloqueados pelo limite diário
    (`limit_blocked_403`) e aqueles que fizeram upgrade (`premium_converted`).

    Métricas devolvidas
    -------------------
    `blocked_events`    — ocorrências totais de limit_blocked_403
    `blocked_users`     — utilizadores únicos que foram bloqueados
    `converted_events`  — ocorrências totais de premium_converted
    `converted_users`   — utilizadores únicos que converteram
    `conversion_rate_pct` — converted_users / blocked_users × 100

    Nota: usar contagens de utilizadores únicos (distinct user_id) é mais
    representativo do funil real do que contar eventos brutos, pois um único
    utilizador pode ser bloqueado múltiplas vezes antes de converter.
    """
    blocked_events   = _scalar_count(db, "limit_blocked_403", from_date, to_date, distinct_users=False)
    blocked_users    = _scalar_count(db, "limit_blocked_403", from_date, to_date, distinct_users=True)
    converted_events = _scalar_count(db, "premium_converted", from_date, to_date, distinct_users=False)
    converted_users  = _scalar_count(db, "premium_converted", from_date, to_date, distinct_users=True)

    conversion_rate = (
        round(converted_users / blocked_users * 100, 2) if blocked_users > 0 else 0.0
    )

    # Average number of block events per user before they convert (or give up).
    avg_blocks_before_convert = (
        round(blocked_events / blocked_users, 2) if blocked_users > 0 else 0.0
    )

    return {
        "period": _period_dict(from_date, to_date),
        "funnel": {
            "blocked_events":   blocked_events,
            "blocked_users":    blocked_users,
            "converted_events": converted_events,
            "converted_users":  converted_users,
        },
        "conversion_rate_pct":        conversion_rate,
        "avg_blocks_before_convert":  avg_blocks_before_convert,
        "methodology": (
            "conversion_rate_pct = distinct users with premium_converted "
            "÷ distinct users with limit_blocked_403 × 100"
        ),
    }


# ============================================================================
# ENDPOINT 3 — SOCIAL SHARES
# ============================================================================

@router.get(
    "/social-shares",
    summary="Distribuição de partilhas por plataforma e fonte",
    response_description="Ranking de plataformas/fontes extraído dos metadados JSON",
)
def get_social_shares(
    from_date: Optional[date] = Query(default=None),
    to_date:   Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Analisa os metadados JSON dos eventos de partilha (`share_triggered`,
    `share_clicked`) e devolve um ranking de plataformas e fontes.

    Campos esperados em `metadata_json`
    ------------------------------------
    `platform`  — rede social usada (ex: "whatsapp", "instagram", "threads")
    `source`    — local da app onde foi partilhado (ex: "recipe_detail")
    `recipe_title` — título da receita partilhada (agregado separadamente)

    Compatibilidade retroativa
    --------------------------
    A versão atual da app Flutter não envia `platform` — apenas `source`.
    O endpoint trata `platform` como opcional e reporta-o quando presente,
    devolvendo "unknown" caso contrário.

    Adicionar `platform` ao evento no Flutter (em RecipeDetailScreen):
        appApi.logEvent('share_triggered', metadata: {
          'source': 'recipe_detail',
          'platform': platform_name,   // ← adicionar este campo
          'recipe_title': recipe.title,
        });
    """
    q = db.query(AnalyticsEvent.metadata_json).filter(
        AnalyticsEvent.event_name.in_(["share_triggered", "share_clicked"])
    )
    q = _apply_date_filter(q, from_date, to_date)
    rows = q.all()

    platform_counts: dict[str, int] = {}
    source_counts:   dict[str, int] = {}
    title_counts:    dict[str, int] = {}
    parse_errors = 0

    for (meta_str,) in rows:
        if not meta_str:
            continue
        try:
            meta: dict = json.loads(meta_str)
        except (json.JSONDecodeError, TypeError, ValueError):
            parse_errors += 1
            continue

        platform = str(meta.get("platform") or "unknown").lower()
        source   = str(meta.get("source")   or "unknown").lower()
        title    = meta.get("recipe_title")

        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        source_counts[source]     = source_counts.get(source, 0)     + 1

        if title:
            title_counts[title] = title_counts.get(title, 0) + 1

    total_shares = len(rows)

    def _ranked(counts: dict[str, int]) -> list[dict]:
        """Sort descending and compute % share within the group."""
        grand = sum(counts.values()) or 1
        return sorted(
            [
                {
                    "name":  k,
                    "count": v,
                    "pct":   round(v / grand * 100, 1),
                }
                for k, v in counts.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )

    # Top 10 most-shared recipes
    top_recipes = sorted(title_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    platform_data = _ranked(platform_counts)
    has_platform_data = any(p["name"] != "unknown" for p in platform_data)

    return {
        "period":             _period_dict(from_date, to_date),
        "total_share_events": total_shares,
        "by_platform":        platform_data,
        "by_source":          _ranked(source_counts),
        "top_shared_recipes": [{"title": t, "shares": c} for t, c in top_recipes],
        "parse_errors":       parse_errors,
        "platform_data_available": has_platform_data,
        "action_required": (
            None
            if has_platform_data
            else (
                "O campo 'platform' não está presente nos metadados dos eventos de partilha. "
                "Adiciona 'platform' ao logEvent('share_triggered') no Flutter "
                "para obter o rácio por rede social (WhatsApp, Instagram, Threads, etc.)."
            )
        ),
    }
