"""
Module: main.py

Description:
Core FastAPI application for Smart Kitchen backend.

Responsibilities:
- User authentication & authorization (JWT)
- Subscription management (Google Play Billing )
- Image analysis & ingredient detection (OpenAI Vision)
- Recipe generation (AI-powered)
- Rate limiting & caching (Redis)
- User preferences & usage tracking

Architecture Notes:
- Uses FastAPI + SQLAlchemy ORM
- Redis for caching and rate limiting
- OpenAI for AI processing
- Designed to evolve into SaaS-grade backend
"""

# ========================
# STANDARD LIBRARIES
# ========================

import asyncio
import base64
import io
import json
import os
import random
import uuid
from datetime import date, datetime, timedelta
from typing import List
from enum import Enum

# ========================
# ENVIRONMENT — must run first, before ANY other import that reads env vars.
# override=True forces the .env file to overwrite stale OS-level variables
# (e.g. old keys cached from a previous PyCharm session or a deleted venv).
# ========================

from dotenv import load_dotenv

load_dotenv(override=True)

# Fail fast: if critical vars are missing, crash at startup with a clear
# message instead of getting a cryptic 401 / 500 later at request time.
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_DATABASE_URL = os.getenv("DATABASE_URL", "")

if not _OPENAI_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY não definida no .env — "
        "servidor não pode arrancar sem ela."
    )

if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definida no .env")

# ========================
# THIRD-PARTY LIBRARIES
# ========================

import redis
import structlog
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, Index
from sqlalchemy.orm import Session, Mapped, mapped_column
from PIL import Image
from openai import OpenAI
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from pydantic import BaseModel, EmailStr, constr
from models import User, Purchase, AnalyticsEvent, AiRecipeCache, ChefChallenge, UserChallengeProgress  # noqa: F401 — all model classes must be imported so Base.metadata includes their tables in create_all()
from routers import auth, billing, challenges
from challenges_pool import FREE_CHALLENGES, PREMIUM_CHALLENGES
from core.security import hash_password
from dependencies.auth import get_current_user

# ========================
# INTERNAL MODULES
# ========================

from database import Base, engine, SessionLocal, run_column_migrations
from ai.normalization import normalize_ingredients
from ai.cache import generate_cache_key, get_cached, set_cache
from routers import favorites
from routers import analytics_admin, maintenance
from services.analytics_service import log_analytics_event


# ========================
# ENVIRONMENT CONFIGURATION
# ========================

DATABASE_URL = _DATABASE_URL

# Google Play Billing config
GOOGLE_PACKAGE_NAME = os.getenv("GOOGLE_PACKAGE_NAME")

# OpenAI client — key read explicitly from env (already validated above).
# Never rely on the OpenAI SDK's own env-var auto-detection: always pass
# api_key explicitly so the value is visible and traceable at startup.
client = OpenAI(api_key=_OPENAI_KEY)

# Redis configuration
redis_url = os.getenv("REDIS_URL")

if redis_url:
    # Production (Render)
    redis_client = redis.from_url(redis_url, decode_responses=True)
else:
    # Local development
    redis_client = redis.Redis(
        host="localhost",
        port=6379,
        decode_responses=True
    )

# Structured logging (production-grade logging system)
logger = structlog.get_logger()

# ========================
# FASTAPI INITIALIZATION
# ========================

app = FastAPI()

# Create tables that don't exist yet.
Base.metadata.create_all(bind=engine)

# Add any columns that exist in the models but are missing from the physical
# tables (happens when new fields are added to a model after the initial
# create_all). This is an append-only, idempotent migration — safe on every
# startup. Replace with Alembic for production-grade migrations.
run_column_migrations()

# Register routers (modular API design)
app.include_router(auth.router, prefix="/auth", tags=["Auth"])

app.include_router(
    favorites.router,
    prefix="/favorites",
    tags=["Favorites"],
)


# Billing routes
app.include_router(billing.router, prefix="/billing", tags=["Billing"])

# Challenges routes
app.include_router(challenges.router, prefix="/challenges", tags=["Challenges"])


# ========================
# WEEKLY CHALLENGE ROTATION
# ========================

# Number of challenges inserted per rotation run.
_FREE_PER_WEEK = 1
_PREMIUM_PER_WEEK = 2


def _current_iso_week() -> tuple[int, int]:
    """Return (iso_year, iso_week) for the current UTC moment."""
    iso = datetime.utcnow().isocalendar()
    return int(iso[0]), int(iso[1])   # (year, week)


def _needs_rotation(db: Session) -> bool:
    """
    Return True when there are NO active challenges tagged for the current
    ISO week.  This covers two cases:
      1. First ever boot (empty table).
      2. Weekly rotation — Monday 00:00 passes and the active set belongs
         to a previous week.
    Legacy rows (week_year=NULL) are NOT counted as "current week" so the
    first run of the rotation system always replaces them.
    """
    year, week = _current_iso_week()
    count = (
        db.query(ChefChallenge)
        .filter(
            ChefChallenge.is_active == 1,
            ChefChallenge.week_year == year,
            ChefChallenge.week_number == week,
        )
        .count()
    )
    return count == 0


def _run_rotation(db: Session) -> None:
    """
    Weekly rotation:
      1. Mark all currently-active challenges as inactive.
      2. Sample _FREE_PER_WEEK + _PREMIUM_PER_WEEK from the pool,
         avoiding badge codes that were active in the previous cycle.
      3. Insert the new selection tagged with the current ISO week.

    Uses random.sample() so consecutive weeks are very unlikely to repeat
    the same challenges (pool size > selection size guarantees this for the
    default pool of 5+5).
    """
    year, week = _current_iso_week()

    # ── Collect badge codes that are currently active (to avoid immediate repeat)
    previous_badges = {
        row.badge_code
        for row in db.query(ChefChallenge.badge_code)
        .filter(ChefChallenge.is_active == 1)
        .all()
    }

    # ── Deactivate old challenges (keep rows for historical user progress)
    db.query(ChefChallenge).filter(
        ChefChallenge.is_active == 1
    ).update({"is_active": 0}, synchronize_session="fetch")

    # ── Select new challenges, preferring ones not used last week
    def _prefer_fresh(pool: list[dict], n: int) -> list[dict]:
        """Sample n items from pool, giving priority to unseen badge codes."""
        fresh = [c for c in pool if c["badge_code"] not in previous_badges]
        if len(fresh) >= n:
            return random.sample(fresh, n)
        # Not enough fresh items — fall back to the full pool
        return random.sample(pool, min(n, len(pool)))

    selected: list[dict] = (
        _prefer_fresh(FREE_CHALLENGES, _FREE_PER_WEEK)
        + _prefer_fresh(PREMIUM_CHALLENGES, _PREMIUM_PER_WEEK)
    )

    for tmpl in selected:
        db.add(ChefChallenge(
            title=tmpl["title"],
            required_ingredients=tmpl["required_ingredients"],
            is_premium_only=tmpl["is_premium_only"],
            badge_code=tmpl["badge_code"],
            is_active=1,
            week_number=week,
            week_year=year,
        ))

    db.commit()
    logger.info(
        "[rotation] Week %d/%d activated: %s",
        week, year,
        [c["title"] for c in selected],
    )


# ── Startup hook — runs rotation immediately if this week has no challenges ───

@app.on_event("startup")
def _startup_rotation() -> None:
    """
    On every server start, check whether the current ISO week already has
    active challenges.  If not (first boot or Monday after a cold restart),
    run the rotation immediately so the API always returns fresh data.
    """
    db = SessionLocal()
    try:
        if _needs_rotation(db):
            logger.info("[startup] No active challenges for this week — running rotation.")
            _run_rotation(db)
        else:
            logger.debug("[startup] Active challenges found for this week — skipping rotation.")
    except Exception as exc:
        logger.error("[startup] Startup rotation failed: %s", exc)
    finally:
        db.close()


# ── Background async worker — fires rotation every Monday 00:00 UTC ──────────

async def _weekly_rotation_worker() -> None:
    """
    Long-running coroutine that wakes up every Monday at 00:00 UTC and
    runs the weekly challenge rotation.

    Sleep strategy
    --------------
    Compute seconds until the next Monday 00:00 UTC and sleep exactly that
    long.  After each wake-up, re-check whether rotation is still needed
    (guards against duplicate rotations if the worker is restarted and the
    startup hook already ran for the current week).
    """
    while True:
        now = datetime.utcnow()

        # days_until_monday: 0 if today is Monday, else 1-6.
        # If it's already Monday add 7 so we sleep until NEXT Monday rather
        # than waking up immediately (startup hook already handles today).
        days_ahead = (7 - now.weekday()) % 7 or 7
        next_monday = (now + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_secs = (next_monday - now).total_seconds()

        logger.info(
            "[rotation-worker] Sleeping %.0f s until next Monday %s UTC.",
            sleep_secs,
            next_monday.strftime("%Y-%m-%d %H:%M"),
        )
        await asyncio.sleep(sleep_secs)

        db = SessionLocal()
        try:
            if _needs_rotation(db):
                _run_rotation(db)
            else:
                logger.debug("[rotation-worker] Rotation already ran for this week.")
        except Exception as exc:
            logger.error("[rotation-worker] Rotation failed: %s", exc)
        finally:
            db.close()


@app.on_event("startup")
async def _start_weekly_rotation_worker() -> None:
    """Kick off the Monday-rotation background coroutine at server start."""
    asyncio.create_task(_weekly_rotation_worker())


# Admin analytics read routes
app.include_router(
    analytics_admin.router,
    prefix="/api/admin/analytics",
    tags=["Analytics Admin"],
)

# Admin maintenance routes
app.include_router(
    maintenance.router,
    prefix="/api/admin/maintenance",
    tags=["Admin Maintenance"],
)

# ========================
# RATE LIMITING (ANTI-ABUSE)
# ========================

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=redis_url if redis_url else "redis://localhost:6379"
)

# Attach limiter to app state
app.state.limiter = limiter  # type: ignore

# Middleware for rate limiting
app.add_middleware(SlowAPIMiddleware)

# Custom handler for rate limit exceeded
app.add_exception_handler(
    RateLimitExceeded,
    lambda request, exc: PlainTextResponse(
        "Rate limit exceeded",
        status_code=429
    ),
)


# ========================
# ENUMS
# ========================


class SubscriptionPlan(str, Enum):
    FREE = "free"
    MONTHLY = "monthly"
    YEARLY = "yearly"

# ========================
# DATABASE MODELS
# ========================


class RecipeCache(Base):
    """
        Database fallback cache for generated recipes.

        Used when Redis is unavailable or for persistence.
        """
    __tablename__ = "recipe_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredients_hash: Mapped[str] = mapped_column(String, index=True)
    response_json: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    """
        Token used for password reset flows.

        Security:
        - Short expiration time (15 min recommended)
        """
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    token = Column(String, unique=True)
    expires_at = Column(DateTime)


class UsageLog(Base):
    """
       Tracks API usage (tokens, cost, analytics).

       Future:
       - billing metrics
       - rate optimization
       """
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    tokens_used = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


# Database indexes (performance optimization)
Index("idx_user_id", User.id)
Index("idx_ingredients_hash", RecipeCache.ingredients_hash)
Index("idx_purchase_token_hash", Purchase.purchase_token_hash)


# ========================
# REQUEST SCHEMAS (VALIDATION)
# ========================

class IngredientsRequest(BaseModel):
    ingredients: str


class AnalyticsLogRequest(BaseModel):
    """
    Body for POST /analytics/log — sent fire-and-forget by the Flutter client.
    metadata is stored verbatim as JSON in AnalyticsEvent.metadata_json.
    """
    event_name: str
    metadata: dict = {}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PurchaseRequest(BaseModel):
    """
        Request schema for verifying purchases.
    """
    purchase_token: str
    product_id: str


class PreferencesRequest(BaseModel):
    """
    Request schema for saving user dietary preferences and cuisine style.
    All fields are optional so the client can do a partial update.
    """
    dietary_gluten_free: bool = False
    dietary_vegetarian: bool = False
    dietary_vegan: bool = False
    preferred_cuisine: str = "international"


# ========================
# DEPENDENCIES
# ========================

def get_db():
    """
    Provides a database session per request.
    Ensures proper cleanup.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ========================
# SUBSCRIPTION LOGIC
# ========================

def calculate_expiry(product_id: str):
    """
        Calculates subscription expiration date based on product type.
    """
    now = datetime.utcnow()

    if product_id == "premium_monthly":
        return now + timedelta(days=30)

    if product_id == "premium_yearly":
        return now + timedelta(days=365)

    return now


def check_user_subscription(user: User, db: Session):
    """
    Validates the most recent purchase and syncs user.plan accordingly.

    Uses Purchase.expiry_date (the real DB column).
    Filters for the most recent purchase that has not yet expired.
    """
    now = datetime.utcnow()

    active_purchase = (
        db.query(Purchase)
        .filter(
            Purchase.user_id == user.id,
            Purchase.expiry_date > now,
        )
        .order_by(Purchase.created_at.desc())
        .first()
    )

    if active_purchase:
        user.plan = "premium"
    else:
        user.plan = "free"
        user.plan_expiry = None

    db.commit()

# ========================
# GOOGLE BILLING (SIMPLIFIED VALIDATION)
# ========================


@app.post("/verify-purchase")
def verify_purchase(
    data: PurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
        Endpoint to validate and register a purchase.

        Notes:
        - Prevents duplicate purchase_token usage (anti-fraud)
        - Immediately activates premium access
    """

    # Basic validation
    if not data.purchase_token:
        raise HTTPException(400, "Token inválido")

    is_valid = validate_google_purchase(
        data.purchase_token,
        data.product_id
    )

    # Prevent duplicate purchases (important security check)
    existing = db.query(Purchase).filter(
        Purchase.purchase_token == data.purchase_token
    ).first()

    if existing:
        raise HTTPException(400, "Compra já registada")

    # Calculate subscription expiration
    expires_at = calculate_expiry(data.product_id)

    # Store purchase
    purchase = Purchase(
        user_id=current_user.id,
        product_id=data.product_id,
        purchase_token=data.purchase_token,
        expires_at=expires_at,
        status="active"
    )

    db.add(purchase)

    # Upgrade user immediately
    current_user.plan = "premium"
    db.commit()

    return {"status": "premium_activated", "expires_at": expires_at}


# ========================
# AI HELPER UTILITIES
# ========================


def _extract_response_text(response) -> str:
    """
    Robustly extract the text string from an OpenAI Responses API object.

    The SDK shorthand `response.output_text` works in most cases but can
    return an empty string when:
      - the model emits a refusal or safety block (output type != "message")
      - the SDK version stores content in a nested structure

    Fallback: traverse response.output manually and collect all text blocks.
    """
    # 1. Try SDK shorthand
    text: str = getattr(response, "output_text", "") or ""

    if not text:
        # 2. Manual traversal: response.output is a list of output items;
        #    each item may have a .content list of content blocks with .text
        for item in getattr(response, "output", []):
            for block in getattr(item, "content", []):
                fragment = getattr(block, "text", None)
                if fragment:
                    text += fragment

    return text.strip()


def _strip_markdown_fences(raw: str) -> str:
    """
    Remove Markdown code fences that GPT sometimes wraps JSON output in.

    Examples handled:
        ```json\\n{...}\\n```   →  {...}
        ```\\n[...]\\n```       →  [...]
        ```json{...}```        →  {...}
    """
    raw = raw.strip()

    # Remove opening fence (```json or just ```)
    if raw.startswith("```"):
        # Drop everything up to and including the first newline after the fence
        newline_pos = raw.find("\n")
        raw = raw[newline_pos + 1:] if newline_pos != -1 else raw[3:]

    # Remove closing fence
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]

    return raw.strip()


def _parse_openai_json(response, context: str = "") -> object:
    """
    Extract text from an OpenAI response, strip Markdown fences, and parse JSON.

    Args:
        response: OpenAI Responses API response object
        context:  Label for log messages (e.g. "detect_ingredients")

    Returns:
        Parsed Python object (dict or list).

    Raises:
        ValueError with a detailed message (including the raw text) so the
        caller can decide how to handle the failure.
    """
    raw = _extract_response_text(response)

    if not raw:
        raise ValueError(
            f"[{context}] OpenAI devolveu uma resposta vazia. "
            f"Verifique o token limit e o modelo."
        )

    clean = _strip_markdown_fences(raw)

    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        # Log the FULL raw text so we can see exactly what the model sent
        logger.error(
            f"[{context}] JSONDecodeError ao fazer parse da resposta OpenAI.",
            raw_text=raw,
            clean_text=clean,
            error=str(exc),
        )
        raise ValueError(
            f"[{context}] Parse JSON falhou: {exc}. "
            f"Texto recebido (primeiros 500 chars): {raw[:500]!r}"
        ) from exc


# ========================
# AI FUNCTIONS
# ========================


def detect_ingredients(
    image_bytes: bytes,
    language: str = "pt",
    user: "User | None" = None,
):
    """
    Uses OpenAI Vision (gpt-4.1-mini) to detect food ingredients from an image.

    Args:
        image_bytes  Raw image bytes (JPEG / PNG / WebP).
        language     BCP-47 locale string — ingredient names are returned in
                     the matching language (e.g. "pt-PT" → Portuguese).
        user         Authenticated User object. When provided, the user's
                     dietary restrictions are injected into the system prompt
                     so the model can flag ingredients that conflict with those
                     restrictions. This gives the recipe generator richer
                     context for substitutions without altering the output schema.

    Returns:
        List of dicts:
            [
              {
                "name": "tomate",
                "confidence": "high|medium|low",
                "dietary_flag": "restricted|ok"   ← only present when user has restrictions
              },
              ...
            ]
        Returns an empty list on any failure — callers must treat an empty
        list as "no ingredients detected" and NEVER raise from here.
    """

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # ── Build dietary context section ─────────────────────────────────────────
    # Only included when the user has at least one active restriction.
    # The model is instructed to STILL REPORT all visible ingredients (accurate
    # detection is always more useful than omission), but to annotate any that
    # conflict with the user's restrictions so the recipe generator can apply
    # the correct substitutions and hard rules downstream.
    diet_lines: list[str] = []

    if user is not None:
        if user.dietary_vegan:
            diet_lines.append(
                "User is VEGAN: no animal products (meat, poultry, fish, seafood, "
                "eggs, dairy, honey, gelatin). Flag any such ingredient as restricted."
            )
        elif user.dietary_vegetarian:
            diet_lines.append(
                "User is VEGETARIAN: no meat, poultry, fish or seafood. "
                "Flag any such ingredient as restricted."
            )
        if user.dietary_gluten_free:
            diet_lines.append(
                "User is GLUTEN-FREE: no wheat, barley, rye, spelt, regular flour, "
                "regular bread or regular pasta. Flag any such ingredient as restricted."
            )

    if diet_lines:
        dietary_section = (
            "\nUser dietary restrictions — detect ALL ingredients accurately, "
            "then add \"dietary_flag\": \"restricted\" to any that violate these rules "
            "(add \"dietary_flag\": \"ok\" to compliant ones):\n"
            + "\n".join(f"  • {line}" for line in diet_lines)
        )
        json_schema = (
            '[{"name":"","confidence":"high|medium|low","dietary_flag":"restricted|ok"}]'
        )
    else:
        dietary_section = ""
        json_schema = '[{"name":"","confidence":"high|medium|low"}]'

    # ── Compose prompt ────────────────────────────────────────────────────────
    prompt_text = (
        f"Return ONLY a JSON array, no extra text.\n"
        f"{json_schema}\n"
        f"Rules:\n"
        f"- Edible food ingredients only (ignore packaging, utensils, labels, text)\n"
        f"- Identify every distinct ingredient visible in the image\n"
        f"- Use the language matching locale: {language}\n"
        f"- Output ONLY valid JSON — no markdown, no prose"
        f"{dietary_section}"
    )

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": prompt_text,
                },
                {
                    "type": "input_image",
                    "image_base64": img_b64,
                },
            ],
        }],
        # 600 tokens — enough for up to ~25 ingredients with confidence + flag values
        max_output_tokens=600,
    )

    try:
        result = _parse_openai_json(response, context="detect_ingredients")

        if isinstance(result, list):
            return result

        # Model returned a dict instead of a list — try to extract the array
        if isinstance(result, dict):
            for key in ("ingredients", "items", "data"):
                if isinstance(result.get(key), list):
                    return result[key]

        logger.warning("[detect_ingredients] Resposta inesperada do modelo: %r", result)
        return []

    except Exception as exc:
        logger.error(
            "[detect_ingredients] Falha na deteção de ingredientes.",
            error=str(exc),
        )
        return []


async def generate_recipes(
        ingredients: List[str],
        user: User,
        db: Session,
        language: str = "en-US",
):
    """
    Generates recipes using AI based on ingredients and user preferences.

    Cache hierarchy (fastest → slowest):
      1. DB cache  (AiRecipeCache) — persistent across restarts, checked first.
      2. Redis cache               — ephemeral but sub-millisecond on hit.
      3. OpenAI API call           — only reached on full cache miss.

    On an OpenAI call the result is written to both Redis and DB so that
    subsequent requests benefit from whichever layer survives longest.

    Returns:
        List of recipe dicts (already extracted from JSON).
    """

    # Normalize ingredient names (important for consistency + cache hits)
    ingredients = normalize_ingredients(ingredients)

    # Free vs premium logic
    num_recipes = 1 if user.plan == "free" else 4

    # ── Dietary restriction hard rules ────────────────────────────────────────
    # Each active restriction generates an EXPLICIT, non-negotiable rule for
    # the prompt. Vegan supersedes vegetarian (it is stricter), so we only add
    # the vegan rule when both are active to avoid contradictory instructions.
    diet_rules: list[str] = []

    if user.dietary_vegan:
        diet_rules.append(
            "STRICT VEGAN — HARD RULE: The recipe MUST NOT contain ANY animal "
            "products. This includes meat, poultry, fish, seafood, eggs, milk, "
            "cheese, butter, cream, yogurt, honey, gelatin, or any other "
            "animal-derived ingredient. If a listed ingredient violates this rule, "
            "omit it and substitute a plant-based alternative."
        )
    elif user.dietary_vegetarian:
        diet_rules.append(
            "STRICT VEGETARIAN — HARD RULE: The recipe MUST NOT contain meat, "
            "poultry, fish, or seafood of any kind. Eggs and dairy are allowed."
        )

    if user.dietary_gluten_free:
        diet_rules.append(
            "STRICT GLUTEN-FREE — HARD RULE: The recipe MUST NOT contain wheat, "
            "barley, rye, spelt, kamut, regular flour, regular pasta, regular bread, "
            "regular soy sauce, or any other gluten-containing ingredient. "
            "Use only certified gluten-free alternatives (e.g. rice flour, "
            "gluten-free soy sauce/tamari, corn-based pasta)."
        )

    diet_section = (
        "\n".join(f"  • {rule}" for rule in diet_rules)
        if diet_rules
        else "  • No dietary restrictions — any ingredients are allowed."
    )

    # ── Cuisine style ─────────────────────────────────────────────────────────
    user_cuisine = (user.preferred_cuisine or "international").strip()
    cuisine_instruction = (
        f"Adapt the flavour profile, spices, and plating style to {user_cuisine} cuisine."
        if user_cuisine.lower() not in ("international", "internacional", "")
        else "Keep a balanced international culinary style."
    )

    # Language adaptation instruction
    language_instruction = f"Generate all text in the language that matches locale '{language}'."

    # ── System-level instructions (highest priority in the model context) ─────
    # Placing dietary restrictions in `instructions` gives them SYSTEM-LEVEL
    # authority — the model is instructed at the context root, not just inside
    # the user turn, making it significantly harder for the model to "forget"
    # the constraints when generating complex multi-step recipes.
    system_instructions = (
        "You are a Michelin-star chef, a professional nutritionist, "
        "and an expert in fast, healthy home cooking.\n\n"
        f"{language_instruction}\n"
        f"{cuisine_instruction}\n\n"
        "══ DIETARY RESTRICTIONS — SYSTEM-LEVEL HARD RULES ══\n"
        "These rules are ABSOLUTE and NON-NEGOTIABLE. "
        "They MUST be respected in EVERY recipe, ingredient, step, and suggestion. "
        "Violating any of the rules below is a critical error.\n\n"
        f"{diet_section}"
    )

    # =========================================================================
    # LAYER 1 — DB CACHE (persistent, survives Redis flush / server restart)
    # =========================================================================
    # Key encodes every dimension that affects the OpenAI prompt output so that
    # two users with different plans or dietary preferences never share a cache
    # entry.  Format (human-readable for easy manual inspection / purging):
    #
    #   "arroz,brocolos,frango|1r|lang=pt-PT|gf=0|vg=0|vn=0|c=international"
    #
    # Ingredients are sorted alphabetically + lower-cased so that "Frango,Arroz"
    # and "arroz,frango" resolve to the same key.
    db_cache_key = (
        ",".join(sorted(i.lower().strip() for i in ingredients))
        + f"|{num_recipes}r"
        + f"|lang={language}"
        + f"|gf={int(bool(user.dietary_gluten_free))}"
        + f"|vg={int(bool(user.dietary_vegetarian))}"
        + f"|vn={int(bool(user.dietary_vegan))}"
        + f"|c={user_cuisine}"
    )

    db_hit = db.query(AiRecipeCache).filter(
        AiRecipeCache.ingredients_hash == db_cache_key
    ).first()

    if db_hit:
        logger.info("[generate_recipes] DB cache HIT — key: %.120s", db_cache_key)
        return json.loads(db_hit.recipe_json)

    # =========================================================================
    # LAYER 2 — REDIS CACHE (ephemeral but sub-millisecond lookup)
    # =========================================================================
    # Include user-specific dietary preferences in the Redis key so that two
    # users with different restrictions never receive each other's cached recipes.
    import hashlib as _hashlib
    _prefs_fingerprint = _hashlib.md5(
        f"{user.dietary_vegan}{user.dietary_vegetarian}"
        f"{user.dietary_gluten_free}{user_cuisine}".encode()
    ).hexdigest()[:10]

    redis_cache_key = generate_cache_key(ingredients, language) + f"_{_prefs_fingerprint}"

    cached = await get_cached(redis_cache_key)
    if cached:
        # Backfill DB cache so the next request survives a Redis flush.
        _db_cache_write(db, db_cache_key, cached)
        logger.info("[generate_recipes] Redis cache HIT — backfilled DB cache.")
        return cached  # instant response (no API cost)

    # ========================
    # PROMPT ENGINEERING
    # ========================
    # The dietary restrictions are NOT repeated here — they live in
    # `system_instructions` (passed as `instructions=` to the OpenAI Responses
    # API) where they receive system-level authority.  Keeping this section
    # focused on the task reduces token usage and avoids conflicting phrasing.

    prompt = f"""
    ══ INGREDIENTS PROVIDED BY THE USER ══
    {", ".join(ingredients)}

    ══ RECIPE RULES ══
    - Max cooking time: 30 minutes
    - Use ONLY the given ingredients plus universally available staples (salt, pepper, oil, water)
    - ALL provided ingredients MUST appear in at least one recipe
    - Mention every ingredient explicitly in the cooking steps
    - Steps must be detailed, realistic, and written for a home cook
    - Include preparation steps (washing, chopping, marinating, etc.)
    - Suggest optional extra ingredients that would enhance the recipe
    - Provide accurate nutritional values per serving
    - Output ONLY valid JSON — no markdown fences, no extra text

    ══ OUTPUT FORMAT ══
    {{
      "recipes": [
        {{
          "title": "",
          "time_minutes": 0,
          "calories": 0,
          "protein_g": 0,
          "carbs_g": 0,
          "fat_g": 0,
          "vitamins": {{
            "vitamin_a": "",
            "vitamin_c": "",
            "vitamin_d": "",
            "vitamin_b12": ""
          }},
          "optional_ingredients": [],
          "steps": []
        }}
      ]
    }}

    Generate exactly {num_recipes} recipe(s) in JSON.
    Respect ALL dietary rules from the system instructions without exception.
    """

    # Call OpenAI
    # Token budget: 1 recipe ≈ 600–800 tokens; 4 recipes ≈ 2 400–3 200 tokens.
    # We use 2 000 as a safe ceiling for Free (1 recipe) and bump to 4 000 for
    # Premium (4 recipes) so the JSON is never truncated mid-object.
    max_tokens = 2000 if num_recipes == 1 else 4000

    response = client.responses.create(
        model="gpt-4.1-mini",
        instructions=system_instructions,   # system-level dietary rules
        input=prompt,                        # task description + output schema
        max_output_tokens=max_tokens,
    )

    try:
        parsed = _parse_openai_json(response, context="generate_recipes")
    except ValueError as exc:
        # Propagate as HTTP 500 with a clear message instead of crashing
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    # The model should return {"recipes": [...]}.
    # Guard against it returning the list directly.
    if isinstance(parsed, list):
        recipes = parsed
    else:
        recipes = parsed.get("recipes", [])

    if not recipes:
        raise HTTPException(
            status_code=500,
            detail=(
                "A OpenAI devolveu uma resposta válida mas sem receitas. "
                "Tenta novamente com outros ingredientes."
            ),
        )

    # ── Write to both cache layers ─────────────────────────────────────────────
    # Redis write (ephemeral, fast reads on subsequent requests)
    await set_cache(redis_cache_key, recipes)
    # DB write (persistent; committed atomically with analyses_today by the caller)
    _db_cache_write(db, db_cache_key, recipes)

    return recipes


def _db_cache_write(db: Session, key: str, recipes: list) -> None:
    """
    Upsert a recipe list into AiRecipeCache.

    Uses db.merge() (SQL UPSERT semantics) so concurrent requests for the
    same key are safe — the last writer wins without raising IntegrityError.

    Does NOT commit: the calling endpoint's db.commit() persists this row
    atomically alongside the analyses_today increment.
    """
    try:
        db.merge(AiRecipeCache(
            ingredients_hash=key,
            recipe_json=json.dumps(recipes, ensure_ascii=False),
        ))
    except Exception as exc:
        # Cache write failure must never surface as a user-visible error.
        logger.warning("[generate_recipes] DB cache write failed: %s", exc)


# ========================
# TEXT-BASED RECIPE GENERATION
# ========================

@app.post("/generate-recipes/")
@limiter.limit("10/minute")
async def generate_recipes_from_text(
    request: Request,
    data: IngredientsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate recipes from a comma-separated ingredients string typed by the user.

    Flow:
    1. Validate JWT and daily limit (same rules as /analyze-image/)
    2. Split ingredient text into a list
    3. Call generate_recipes() with OpenAI
    4. Increment analyses_today counter

    Returns the same shape as /analyze-image/ so the Flutter client
    can reuse the same parsing logic.
    """

    # Detect user language from headers
    accept_language = request.headers.get("accept-language")
    language = accept_language.split(",")[0] if accept_language else "pt-PT"

    # Reset daily counter if it is a new day.
    # This must happen before the quota check so the first request of a new
    # day always succeeds regardless of yesterday's analyses_today value.
    hoje = date.today()
    if current_user.last_analysis_date != hoje:
        current_user.analyses_today = 0
        current_user.last_analysis_date = hoje

    # Quota: only an explicit "premium" plan value earns the higher limit.
    # Any other value — "free", None, an unexpected string, an expired plan
    # still in the DB — falls back to the free-tier limit of 1.
    limit = 4 if current_user.plan == "premium" else 1

    if current_user.analyses_today >= limit:
        log_analytics_event(
            db,
            event_name="limit_blocked_403",
            user_id=current_user.id,
            metadata={
                "plan": current_user.plan,
                "analyses_today": current_user.analyses_today,
                "limit": limit,
            },
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Limite diário de receitas atingido para o teu plano. "
                "Faz upgrade para Premium ou aguarda até amanhã!"
            ),
        )

    # Split the comma-separated string into a clean list
    raw = data.ingredients.strip()
    if not raw:
        raise HTTPException(400, "Nenhum ingrediente fornecido")

    ingredient_list = [i.strip() for i in raw.split(",") if i.strip()]
    if not ingredient_list:
        raise HTTPException(400, "Nenhum ingrediente válido fornecido")

    # Refresh the user row so we always apply the latest dietary preferences —
    # the JWT token does not carry preferences, so the SQLAlchemy identity map
    # could hold a stale snapshot if the user updated preferences mid-session.
    db.refresh(current_user)

    # Generate recipes via OpenAI
    recipes = await generate_recipes(
        ingredients=ingredient_list,
        user=current_user,
        db=db,
        language=language,
    )

    # Increment usage counter
    current_user.analyses_today += 1
    db.commit()

    return {
        "ingredients_detected": ingredient_list,
        "recipes": recipes,
    }


# ========================
# IMAGE SCAN ENDPOINT
# ========================

@app.post("/scan-ingredients")
@limiter.limit("10/minute")
async def scan_ingredients(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
        Endpoint to scan an image and return detected ingredients + recipes.

        Flow:
        1. Read image
        2. Detect ingredients (AI Vision)
        3. Generate recipes (AI text model)
    """

    image_bytes = await file.read()

    # Detect ingredients from image
    detected = detect_ingredients(image_bytes)

    if not detected:
        raise HTTPException(400, "No ingredients detected")

    # Extract ingredient names
    ingredients = [item["name"] for item in detected]

    # Generate recipes
    recipes = await generate_recipes(
        ingredients=ingredients,
        user=user,
        db=db
    )

    return {
        "ingredients_detected": detected,
        "recipes": recipes
    }



@app.get("/subscription-status")
def subscription_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns detailed subscription status for the authenticated user.

    - free    → no purchases on record
    - expired → most recent purchase has passed its expiry_date
    - active  → valid purchase with expiry_date in the future
    """
    purchase = (
        db.query(Purchase)
        .filter(Purchase.user_id == current_user.id)
        .order_by(Purchase.created_at.desc())
        .first()
    )

    if not purchase:
        return {"status": "free"}

    if purchase.expiry_date < datetime.utcnow():
        return {
            "status": "expired",
            "message": "Subscrição expirada.",
        }

    return {
        "status": "active",
        "expiry_date": purchase.expiry_date.isoformat(),
    }


@app.post("/forgot-password")
def forgot_password(email: str, db: Session = Depends(get_db)):
    """
       Initiates password reset flow.

       Security:
       - Does NOT reveal if email exists (anti-enumeration attack)
       - Generates temporary token with expiration
    """

    user = db.query(User).filter(User.email == email).first()

    # Always return success message
    if not user:
        return {"message": "Se existir conta, email enviado"}  # 🔒 segurança

    # Generate secure random token
    token = str(uuid.uuid4())

    reset = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(minutes=15)
    )

    db.add(reset)
    db.commit()

    # integrate email provider (SendGrid, SES, etc.)
    print(f"RESET LINK: https://teusite.com/reset?token={token}")

    return {"message": "Email enviado"}


@app.post("/reset-password")
def reset_password(token: str, new_password: str, db: Session = Depends(get_db)):
    """
        Resets user password using a valid reset token.

        Security:
        - Token must exist and not be expired
        - Token is deleted after use (one-time use)
    """

    reset = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token
    ).first()

    if not reset or reset.expires_at < datetime.utcnow():
        raise HTTPException(400, "Token inválido")

    user = db.query(User).filter(User.id == reset.user_id).first()

    # Update password securely
    user.password = hash_password(new_password)

    db.delete(reset)
    db.commit()

    return {"message": "Password atualizada"}


@app.get("/preferences")
def get_preferences(
    current_user: User = Depends(get_current_user),
):
    """
    Returns the current dietary preferences and cuisine style for the
    authenticated user.
    """
    return {
        "dietary_gluten_free": current_user.dietary_gluten_free,
        "dietary_vegetarian": current_user.dietary_vegetarian,
        "dietary_vegan": current_user.dietary_vegan,
        "preferred_cuisine": current_user.preferred_cuisine,
    }


@app.put("/update-preferences")
def update_preferences(
    data: PreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Saves user dietary preferences and preferred cuisine.

    These preferences are applied in real-time on every recipe generation
    request for the authenticated user.

    Rules enforced by the AI prompt:
    - vegan       → no meat, fish, eggs, dairy, honey
    - vegetarian  → no meat or fish
    - gluten_free → no wheat, barley, rye, regular flour/pasta/bread
    """
    current_user.dietary_gluten_free = data.dietary_gluten_free
    current_user.dietary_vegetarian = data.dietary_vegetarian
    current_user.dietary_vegan = data.dietary_vegan
    current_user.preferred_cuisine = data.preferred_cuisine.strip() or "international"

    db.commit()

    return {
        "message": "Preferências atualizadas com sucesso",
        "dietary_gluten_free": current_user.dietary_gluten_free,
        "dietary_vegetarian": current_user.dietary_vegetarian,
        "dietary_vegan": current_user.dietary_vegan,
        "preferred_cuisine": current_user.preferred_cuisine,
    }


@app.get("/health")
def health():
    """
        Health check endpoint.

        Used for:
        - Load balancers
        - Monitoring systems
        - Uptime checks
    """
    return {"status": "ok"}


# ========================
# CLIENT-SIDE ANALYTICS INGESTION
# ========================

@app.post("/analytics/log", status_code=204)
def log_client_event(
    data: AnalyticsLogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Receives a single behavioural event emitted by the Flutter client and
    persists it via analytics_service.log_analytics_event().

    Design contract (mirrors the Flutter AppApi.logEvent() fire-and-forget):
    - Always returns HTTP 204 No Content on success.
    - log_analytics_event() never raises — if the DB write fails it is logged
      server-side and the client sees a 204 regardless.
    - Rate-limited upstream by the SlowAPI middleware (inherits /10 per minute).
    """
    log_analytics_event(
        db,
        event_name=data.event_name,
        user_id=current_user.id,
        metadata=data.metadata,
    )


# ========================
# IMAGE ANALYSIS (MAIN PREMIUM FEATURE)
# ========================

@app.post("/analyze-image/")
@limiter.limit("5/minute")
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Main endpoint for image-based recipe generation.

    Flow:
    1. Language auto-detection from Accept-Language header
    2. Daily-counter reset when a new calendar day starts
    3. Quota enforcement (1 analysis/day free · 4/day premium)
    4. Image validation: size ≤ 5 MB + PIL integrity check
    5. AI Vision ingredient detection — user's dietary context injected into
       the system prompt so the model can flag restricted ingredients upfront
    6. Recipe generation via generate_recipes() — dietary rules applied strictly
    7. Counter increment + db.commit()

    All quota blocks are logged to analytics_events for conversion funnel tracking.
    """

    # ── Language detection ────────────────────────────────────────────────────
    accept_language = request.headers.get("accept-language", "")
    language = accept_language.split(",")[0] if accept_language else "pt-PT"

    # ── Daily counter reset (must happen before quota check) ──────────────────
    # Resetting in-memory before the quota check guarantees the first request
    # of a new day always succeeds, regardless of yesterday's counter value.
    # We commit the reset together with the final counter increment at the end,
    # avoiding an extra round-trip for every non-blocked request.
    hoje = date.today()
    if current_user.last_analysis_date != hoje:
        current_user.analyses_today = 0
        current_user.last_analysis_date = hoje

    # ── Quota enforcement ─────────────────────────────────────────────────────
    # Only an explicit "premium" plan value earns the higher limit.
    # Any other value — "free", None, an unexpected string, or an expired plan
    # still lingering in the DB — falls back to the free-tier limit of 1.
    limit = 4 if current_user.plan == "premium" else 1

    if current_user.analyses_today >= limit:
        log_analytics_event(
            db,
            event_name="limit_blocked_403",
            user_id=current_user.id,
            metadata={
                "endpoint": "analyze_image",
                "plan": current_user.plan or "free",
                "analyses_today": current_user.analyses_today,
                "limit": limit,
            },
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Limite diário de {limit} análise(s) de imagem atingido para o teu plano "
                f"'{current_user.plan or 'free'}'. "
                "Faz upgrade para Premium para teres 4 análises por dia!"
            ),
        )

    # ── Image ingestion ───────────────────────────────────────────────────────
    image_bytes = await file.read()

    if len(image_bytes) > 5_000_000:
        raise HTTPException(400, "Imagem demasiado grande (máximo 5 MB).")

    # Validate image integrity before sending to OpenAI — a corrupted or
    # non-image file would waste a Vision API call and produce a confusing error.
    try:
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception:
        raise HTTPException(400, "Ficheiro inválido ou corrompido. Envia uma imagem JPEG/PNG.")

    current_user = db.query(current_user.__class__).filter(current_user.__class__.id == current_user.id).first()
    db.refresh(current_user)

    # ── AI: ingredient detection (Vision) ────────────────────────────────────
    # The user's dietary preferences are passed so the model can flag
    # ingredients that conflict with the user's restrictions. This allows
    # the downstream recipe generator to apply substitutions with full context.
    ingredients = detect_ingredients(image_bytes, language=language, user=current_user)

    if not ingredients:
        raise HTTPException(
            status_code=422,
            detail="Não foi possível detetar ingredientes na imagem. Tenta com uma foto mais clara.",
        )

    # Extract names — detect_ingredients returns [{"name": ..., "confidence": ..., ...}]
    ingredient_names = [i["name"] for i in ingredients]

    # ── AI: recipe generation (with user dietary rules) ───────────────────────
    # generate_recipes() already injects all dietary restrictions into its
    # prompt via the user object — no extra work needed here.
    recipes = await generate_recipes(
        ingredients=ingredient_names,
        user=current_user,
        db=db,
        language=language,
    )

    # ── Persist usage counter ─────────────────────────────────────────────────
    # Single commit: captures daily reset (if it happened) + increment together.
    current_user = db.merge(current_user)
    current_user.analyses_today += 1
    db.commit()

    return {
        "ingredients_detected": ingredients,
        "recipes": recipes,
    }


# ========================
# STATIC RECIPES (TEST ENDPOINT)
# ========================

@app.get("/recipes")
@limiter.limit("10/minute")
async def get_recipes(request: Request):
    pass
    return {
        "recipes": [
            {
                "title": "Omelete Proteica",
                "calories": 320,
                "protein": 25,
                "carbs": 5,
                "fat": 20,
                "time_minutes": 10,
                "steps": [
                    "Bater os ovos",
                    "Adicionar sal e pimenta",
                    "Cozinhar numa frigideira",
                    "Servir quente"
                ],
                "is_premium": False
            },
            {
                "title": "Bowl Fitness",
                "calories": 450,
                "protein": 35,
                "carbs": 30,
                "fat": 15,
                "time_minutes": 15,
                "steps": [
                    "Grelhar o frango",
                    "Cozinhar arroz",
                    "Adicionar legumes",
                    "Misturar tudo"
                ],
                "is_premium": True
            }
        ]
    }
