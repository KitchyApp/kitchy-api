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

import base64
import io
import json
import os
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
from models import User, Purchase, AnalyticsEvent  # noqa: F401 — AnalyticsEvent must be imported so Base.metadata includes analytics_events in create_all()
from routers import auth, billing
from core.security import hash_password
from dependencies.auth import get_current_user

# ========================
# INTERNAL MODULES
# ========================

from database import Base, engine, SessionLocal, run_column_migrations
from ai.normalization import normalize_ingredients
from ai.cache import generate_cache_key, get_cached, set_cache
from routers import favorites
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


def detect_ingredients(image_bytes: bytes, language: str = "pt"):
    """
    Uses OpenAI Vision to detect ingredients from an image.

    Returns a list of dicts: [{"name": "...", "confidence": "high|medium|low"}]
    Returns an empty list if detection fails (never raises — callers treat
    an empty list as "no ingredients found").
    """

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Return ONLY a JSON array, no extra text.\n"
                        "[{\"name\":\"\",\"confidence\":\"high|medium|low\"}]\n"
                        "Rules:\n"
                        "- edible food only\n"
                        "- ignore any text in image\n"
                        f"- language: {language}"
                    )
                },
                {
                    "type": "input_image",
                    "image_base64": img_b64
                }
            ]
        }],
        # 500 tokens — enough for up to ~20 ingredients with confidence values
        max_output_tokens=500
    )

    try:
        result = _parse_openai_json(response, context="detect_ingredients")
        if isinstance(result, list):
            return result
        # Model returned a dict instead of a list — try to extract array
        if isinstance(result, dict):
            for key in ("ingredients", "items", "data"):
                if isinstance(result.get(key), list):
                    return result[key]
        return []
    except Exception as exc:
        logger.error("[detect_ingredients] Falha na deteção de ingredientes.", error=str(exc))
        return []


async def generate_recipes(
        ingredients: List[str],
        user: User,
        db: Session,
        language: str = "en-US",
):
    """
        Generates recipes using AI based on ingredients and user preferences.

        Features:
        - Ingredient normalization
        - Dietary restrictions support
        - Multi-language support
        - Redis caching (performance optimization)

        Returns:
            List of recipes (already extracted from JSON response)
    """

    # Normalize ingredient names (important for consistency + cache hits)
    ingredients = normalize_ingredients(ingredients)

    # Free vs premium logic
    num_recipes = 1 if user.plan == "free" else 4

    # Build dietary restrictions dynamically
    restrictions = []
    user_style = user.preferred_style
    user_cuisine = user.preferred_cuisine

    if user.dietary_gluten_free:
        restrictions.append("gluten free")

    if user.dietary_vegetarian:
        restrictions.append("vegetarian")

    if user.dietary_vegan:
        restrictions.append("vegan")

    restrictions_text = ", ".join(restrictions) if restrictions else "none"

    # Language adaptation instruction
    language_instruction = f"Generate recipes in {language}. Adapt the culinary style to that country."

    # ========================
    # REDIS CACHE (CRITICAL FOR COST + SPEED)
    # ========================

    cache_key = generate_cache_key(ingredients, language)

    cached = await get_cached(cache_key)
    if cached:
        return cached  # instant response (no API cost)

    # ========================
    # PROMPT ENGINEERING
    # ========================

    prompt = f"""
    You are:
    - Michelin chef
    - Professional nutritionist
    - Expert in fast cooking

    {language_instruction}

    User preferences:
    - Style: {user_style}
    - Cuisine: {user_cuisine}

    Ingredients:
    {", ".join(ingredients)}

    Dietary restrictions:
    {restrictions_text}

    Rules:
    - Max cooking time 30 minutes
    - Use ONLY given ingredients + basic staples
    - ALL ingredients MUST be used in the recipe
    - Mention ingredients explicitly in the cooking steps
    - Steps must be detailed and realistic
    - Recipes must feel like real cooking instructions
    - Include preparation steps for ingredients
    - Suggest optional extra ingredients to improve the recipe
    - Provide nutritional values
    - Output ONLY JSON

    Format:
    {{
      "recipes":[
        {{
          "title":"",
          "time_minutes":0,
          "calories":0,
          "protein_g":0,
          "carbs_g":0,
          "fat_g":0,
          
          "vitamins":{{
            "vitamin_a":"",
            "vitamin_c":"",
            "vitamin_d":"",
            "vitamin_b12":""
          }},
          "optional_ingredients":[],
          
          "steps":[]
        }}
      ]
    }}

    Generate {num_recipes} recipes in JSON.
    """

    # Call OpenAI
    # Token budget: 1 recipe ≈ 600–800 tokens; 4 recipes ≈ 2 400–3 200 tokens.
    # We use 2 000 as a safe ceiling for Free (1 recipe) and bump to 4 000 for
    # Premium (4 recipes) so the JSON is never truncated mid-object.
    max_tokens = 2000 if num_recipes == 1 else 4000

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
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

    # Store in cache (store only useful data)
    await set_cache(cache_key, recipes)

    return recipes


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


@app.post("/update-preferences")
def update_preferences(
    gluten_free: bool,
    vegetarian: bool,
    vegan: bool,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
        Updates user dietary preferences.

        These preferences directly affect recipe generation.
    """

    current_user.dietary_gluten_free = gluten_free
    current_user.dietary_vegetarian = vegetarian
    current_user.dietary_vegan = vegan

    db.commit()

    return {"message": "Preferências atualizadas"}


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
# IMAGE ANALYSIS (MAIN PREMIUM FEATURE)
# ========================

@app.post("/analyze-image/")
@limiter.limit("5/minute")
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
       Main endpoint for image-based recipe generation.

       Features:
       - Language auto-detection
       - Daily usage limits (free vs premium)
       - Image validation (size + format)
       - AI ingredient detection + recipe generation
    """

    # Detect user language from headers
    accept_language = request.headers.get("accept-language")

    if accept_language:
        language = accept_language.split(",")[0]
    else:
        language = "en-US"

    # Reset daily usage counter if new day
    hoje = date.today()

    if current_user.last_analysis_date != hoje:
        current_user.analyses_today = 0
        current_user.last_analysis_date = hoje

    # Define daily limits
    limit = 1 if current_user.plan == "free" else 4

    if current_user.analyses_today >= limit:
        raise HTTPException(403, "Limite diário atingido")

    image_bytes = await file.read()

    # Security: file size limit (~5MB)
    if len(image_bytes) > 5_000_000:
        raise HTTPException(400, "Imagem demasiado grande")

    # Validate image integrity
    try:
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception:
        raise HTTPException(400, "Imagem inválida")

    # Detect ingredients via AI
    ingredients = detect_ingredients(image_bytes)

    # Extract names for recipe generation
    ingredient_names = [i["name"] for i in ingredients]

    # Generate recipes
    recipes = await generate_recipes(
        ingredient_names,
        current_user,
        db,
        language=language
    )

    # Update usage counter
    current_user.analyses_today += 1
    db.commit()

    return {
        "ingredients_detected": ingredients,
        "recipes": recipes
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
