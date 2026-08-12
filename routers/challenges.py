"""
Challenges Router
-----------------
GET  /challenges                      — list all challenges + user progress
POST /challenges/{challenge_id}/verify — check if a recipe satisfies a challenge
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import User
from models.challenges import ChefChallenge, UserChallengeProgress

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    """Ingredients that the user actually used in the generated recipe."""
    ingredients: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# LOCALISATION (pt / en / es) — display only; verify still uses DB slugs
# ─────────────────────────────────────────────────────────────────────────────

_TITLE_I18N: dict[str, dict[str, str]] = {
    "badge_tomato": {
        "pt": "Rei do Tomate & Manjericão",
        "en": "Tomato & Basil King",
        "es": "Rey del Tomate y Albahaca",
    },
    "badge_chickpea": {
        "pt": "Mestre das Leguminosas",
        "en": "Legume Master",
        "es": "Maestro de las Legumbres",
    },
    "badge_tuna": {
        "pt": "Caçador de Atum",
        "en": "Tuna Hunter",
        "es": "Cazador de Atún",
    },
    "badge_lentil": {
        "pt": "Herói das Lentilhas",
        "en": "Lentil Hero",
        "es": "Héroe de las Lentejas",
    },
    "badge_egg": {
        "pt": "Rei dos Ovos",
        "en": "Egg King",
        "es": "Rey de los Huevos",
    },
    "badge_protein": {
        "pt": "Monstro do Ginásio",
        "en": "Gym Monster",
        "es": "Monstruo del Gimnasio",
    },
    "badge_gourmet": {
        "pt": "Chef de Elite",
        "en": "Elite Chef",
        "es": "Chef de Élite",
    },
    "badge_rosemary": {
        "pt": "Lombo & Alecrim",
        "en": "Pork & Rosemary",
        "es": "Lomo y Romero",
    },
    "badge_mediterranean": {
        "pt": "Rei do Mediterrâneo",
        "en": "Mediterranean King",
        "es": "Rey del Mediterráneo",
    },
    "badge_mushroom": {
        "pt": "O Cogumelo Místico",
        "en": "The Mystic Mushroom",
        "es": "El Champiñón Místico",
    },
    "badge_gin_tonic": {
        "pt": "Mestre do Gin Tónico",
        "en": "Gin & Tonic Master",
        "es": "Maestro del Gin Tonic",
    },
    "badge_mojito": {
        "pt": "Rei do Mojito",
        "en": "Mojito King",
        "es": "Rey del Mojito",
    },
    "badge_citrus": {
        "pt": "Caçador de Citrus",
        "en": "Citrus Hunter",
        "es": "Cazador de Cítricos",
    },
    "badge_negroni": {
        "pt": "Negroni Clássico",
        "en": "Classic Negroni",
        "es": "Negroni Clásico",
    },
    "badge_whisky": {
        "pt": "Bartender de Elite",
        "en": "Elite Bartender",
        "es": "Bartender de Élite",
    },
    "badge_margarita": {
        "pt": "Mestre da Margarita",
        "en": "Margarita Master",
        "es": "Maestro de la Margarita",
    },
}

_INGREDIENT_I18N: dict[str, dict[str, str]] = {
    "tomate": {"pt": "tomate", "en": "tomato", "es": "tomate"},
    "manjericao": {"pt": "manjericão", "en": "basil", "es": "albahaca"},
    "grao-de-bico": {"pt": "grão-de-bico", "en": "chickpea", "es": "garbanzo"},
    "espinafres": {"pt": "espinafres", "en": "spinach", "es": "espinacas"},
    "atum": {"pt": "atum", "en": "tuna", "es": "atún"},
    "lentilhas": {"pt": "lentilhas", "en": "lentils", "es": "lentejas"},
    "cenoura": {"pt": "cenoura", "en": "carrot", "es": "zanahoria"},
    "ovos": {"pt": "ovos", "en": "eggs", "es": "huevos"},
    "frango": {"pt": "frango", "en": "chicken", "es": "pollo"},
    "salmao": {"pt": "salmão", "en": "salmon", "es": "salmón"},
    "abacate": {"pt": "abacate", "en": "avocado", "es": "aguacate"},
    "lombo de porco": {"pt": "lombo de porco", "en": "pork loin", "es": "lomo de cerdo"},
    "alecrim": {"pt": "alecrim", "en": "rosemary", "es": "romero"},
    "bacalhau": {"pt": "bacalhau", "en": "cod", "es": "bacalao"},
    "azeitonas": {"pt": "azeitonas", "en": "olives", "es": "aceitunas"},
    "cogumelos": {"pt": "cogumelos", "en": "mushrooms", "es": "champiñones"},
    "carne picada": {"pt": "carne picada", "en": "minced meat", "es": "carne picada"},
    "gin": {"pt": "gin", "en": "gin", "es": "ginebra"},
    "tonic": {"pt": "tónica", "en": "tonic", "es": "tónica"},
    "rum": {"pt": "rum", "en": "rum", "es": "ron"},
    "hortela": {"pt": "hortelã", "en": "mint", "es": "menta"},
    "limao": {"pt": "limão", "en": "lemon", "es": "limón"},
    "vodka": {"pt": "vodka", "en": "vodka", "es": "vodka"},
    "campari": {"pt": "campari", "en": "campari", "es": "campari"},
    "vermut": {"pt": "vermut", "en": "vermouth", "es": "vermut"},
    "whisky": {"pt": "whisky", "en": "whisky", "es": "whisky"},
    "bitters": {"pt": "bitters", "en": "bitters", "es": "amargos"},
    "tequila": {"pt": "tequila", "en": "tequila", "es": "tequila"},
}


def _resolve_lang(request: Request, language: Optional[str] = None) -> str:
    """Resolve display language from query param or Accept-Language header (pt/en/es)."""
    raw = (language or "").strip()
    if not raw:
        raw = request.headers.get("accept-language") or request.headers.get("Accept-Language") or ""
    if not raw:
        return "pt"
    tag = raw.split(",")[0].split(";")[0].strip()
    code = tag.replace("_", "-").split("-")[0].lower()
    return code if code in ("pt", "en", "es") else "pt"


def _translate_title(badge_code: str, fallback: str, lang: str) -> str:
    """Return the localised challenge title for badge_code, or fallback if unmapped."""
    entry = _TITLE_I18N.get(badge_code)
    if not entry:
        return fallback
    return entry.get(lang) or entry.get("pt") or fallback


def _translate_ingredients(required_csv: str, lang: str) -> str:
    """Localise a comma-separated ingredient slug list for API display."""
    parts = []
    for token in required_csv.split(","):
        key = token.strip().lower()
        if not key:
            continue
        mapped = _INGREDIENT_I18N.get(key, {})
        parts.append(mapped.get(lang) or mapped.get("pt") or token.strip())
    return ",".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Strip, lowercase, and remove common diacritics for fuzzy matching."""
    return (
        text.strip()
        .lower()
        .replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
        .replace("é", "e").replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


def _ingredients_satisfied(required_csv: str, provided: List[str]) -> tuple[bool, list[str]]:
    """
    Returns (all_satisfied, missing_list).

    Each required token is checked against the normalised provided list.
    A required token is considered matched if any provided ingredient
    *contains* it (e.g. "grao-de-bico" matches "grao-de-bico cozido").
    """
    required_tokens = [_normalise(t) for t in required_csv.split(",") if t.strip()]
    normalised_provided = [_normalise(p) for p in provided]

    missing = [
        req for req in required_tokens
        if not any(req in prov for prov in normalised_provided)
    ]

    return (len(missing) == 0, missing)


# ─────────────────────────────────────────────────────────────────────────────
# GET /challenges
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
def list_challenges(
    request: Request,
    language: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return active challenges grouped for the Flutter Challenges TabBar.

    Response shape:
      {
        "culinary": [ ...challenge cards... ],
        "barman":   [ ...challenge cards... ]
      }

    Titles and required_ingredients are localised from Accept-Language
    (or the `language` query param) before the JSON is built.

    For Free users (`plan != "premium"`), premium-only challenges are
    returned with `is_locked: true` and `badge_code` redacted.
    """
    lang = _resolve_lang(request, language)
    is_premium = current_user.plan == "premium"

    challenges = (
        db.query(ChefChallenge)
        .filter(ChefChallenge.is_active == 1)
        .order_by(
            ChefChallenge.category.asc(),
            ChefChallenge.is_premium_only.asc(),
            ChefChallenge.id.asc(),
        )
        .all()
    )

    # Batch-load all progress rows for this user to avoid N+1 queries.
    progress_map: dict[int, UserChallengeProgress] = {
        p.challenge_id: p
        for p in db.query(UserChallengeProgress)
        .filter(UserChallengeProgress.user_id == current_user.id)
        .all()
    }

    culinary: list[dict] = []
    barman: list[dict] = []

    for ch in challenges:
        is_locked = ch.is_premium_only and not is_premium
        progress = progress_map.get(ch.id)
        category = (getattr(ch, "category", None) or "culinary").strip().lower()
        if category not in ("culinary", "barman"):
            category = "culinary"

        card = {
            "id":                   ch.id,
            "title":                _translate_title(ch.badge_code, ch.title, lang),
            "required_ingredients": _translate_ingredients(ch.required_ingredients, lang),
            "category":             category,
            "is_barman":            category == "barman",
            "is_premium_only":      ch.is_premium_only,
            "is_locked":            is_locked,
            # Hide the badge for locked challenges so it cannot be scraped.
            "badge_code":           ch.badge_code if not is_locked else "🔒",
            "is_completed":         bool(progress and progress.is_completed),
            "completed_at": (
                progress.completed_at.isoformat()
                if (progress and progress.completed_at)
                else None
            ),
            "week_number": ch.week_number,
            "week_year":   ch.week_year,
        }

        if category == "barman":
            barman.append(card)
        else:
            culinary.append(card)

    return {
        "culinary": culinary,
        "barman": barman,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /challenges/{challenge_id}/verify
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{challenge_id}/verify")
def verify_challenge(
    challenge_id: int,
    data: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Check whether the ingredients used satisfy a challenge's requirements.

    Called by the Flutter client immediately after a recipe is generated,
    passing the detected / typed ingredients as the request body.

    Returns:
        completed        (bool)      — True when all required ingredients matched.
        badge_code       (str|null)  — Emoji badge awarded on first completion.
        already_completed (bool)     — True if the user had already won this badge.
        missing          (list[str]) — Required ingredients that were not matched
                                       (only present when completed == False).
    """
    challenge = db.query(ChefChallenge).filter(ChefChallenge.id == challenge_id).first()

    if not challenge:
        raise HTTPException(status_code=404, detail="Desafio não encontrado.")

    # Premium-only challenges are not verifiable by free users.
    if challenge.is_premium_only and current_user.plan != "premium":
        raise HTTPException(
            status_code=403,
            detail="Este desafio é exclusivo para utilizadores Premium.",
        )

    satisfied, missing = _ingredients_satisfied(
        challenge.required_ingredients, data.ingredients
    )

    if not satisfied:
        return {
            "completed":  False,
            "badge_code": None,
            "missing":    missing,
        }

    # ── Challenge satisfied ────────────────────────────────────────────────────
    existing = (
        db.query(UserChallengeProgress)
        .filter(
            UserChallengeProgress.user_id == current_user.id,
            UserChallengeProgress.challenge_id == challenge_id,
        )
        .first()
    )

    if existing and existing.is_completed:
        return {
            "completed":          True,
            "badge_code":         challenge.badge_code,
            "already_completed":  True,
        }

    # First-time completion — record it.
    if existing:
        existing.is_completed = True
        existing.completed_at = datetime.utcnow()
    else:
        db.add(UserChallengeProgress(
            user_id=current_user.id,
            challenge_id=challenge_id,
            is_completed=True,
            completed_at=datetime.utcnow(),
        ))

    db.commit()

    logger.info(
        "Challenge %d completed by user %d — badge: %s",
        challenge_id, current_user.id, challenge.badge_code,
    )

    return {
        "completed":         True,
        "badge_code":        challenge.badge_code,
        "already_completed": False,
    }
