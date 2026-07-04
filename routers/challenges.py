"""
Challenges Router
-----------------
GET  /challenges                      — list all challenges + user progress
POST /challenges/{challenge_id}/verify — check if a recipe satisfies a challenge
"""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return all challenges with the authenticated user's progress.

    For Free users (`plan != "premium"`), premium-only challenges are
    returned with `is_locked: true` and `badge_code` redacted so the
    frontend can render a paywall overlay without a separate request.
    """
    is_premium = current_user.plan == "premium"

    challenges = db.query(ChefChallenge).order_by(ChefChallenge.id).all()

    # Batch-load all progress rows for this user to avoid N+1 queries.
    progress_map: dict[int, UserChallengeProgress] = {
        p.challenge_id: p
        for p in db.query(UserChallengeProgress)
        .filter(UserChallengeProgress.user_id == current_user.id)
        .all()
    }

    result = []
    for ch in challenges:
        is_locked = ch.is_premium_only and not is_premium
        progress = progress_map.get(ch.id)

        result.append({
            "id":                   ch.id,
            "title":                ch.title,
            "required_ingredients": ch.required_ingredients,
            "is_premium_only":      ch.is_premium_only,
            "is_locked":            is_locked,
            # Hide the badge emoji for locked challenges so it can't be
            # scraped from the API response to show in the UI for free.
            "badge_code":           ch.badge_code if not is_locked else "🔒",
            "is_completed":         bool(progress and progress.is_completed),
            "completed_at": (
                progress.completed_at.isoformat()
                if (progress and progress.completed_at)
                else None
            ),
        })

    return result


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
