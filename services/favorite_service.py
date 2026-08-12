"""
Favorite Service Layer

Persists and retrieves user-saved recipes stored as JSON blobs in the
favorites table. Each row belongs to a single user and is keyed by
recipe_title for idempotent saves.

Functions:
- add_favorite       — create or return an existing favourite (same user + title)
- get_user_favorites — list favourites newest-first, skipping corrupted rows
- delete_favorite    — remove a favourite scoped to the owning user_id
"""

from sqlalchemy.orm import Session
import json
from models.favorite import Favorite


# ============================================================================
# ADD FAVORITE
# ============================================================================

def add_favorite(
    db: Session,
    user_id: int,
    recipe_title: str,
    recipe_data: dict,
) -> dict:
    """
    Save a recipe to the user's favourites, or return the existing row.

    Idempotent: if the same user already saved a recipe with the same title,
    the stored row is returned without creating a duplicate.

    Returns a plain dict (id, recipe_title, recipe_data) suitable for
    FastAPI response serialization without ORM mode.
    """
    # Reuse an existing favourite for this user + title (idempotent save).
    existing = (
        db.query(Favorite)
        .filter(
            Favorite.user_id == user_id,
            Favorite.recipe_title == recipe_title,
        )
        .first()
    )

    if existing:
        try:
            stored = json.loads(existing.recipe_data)
        except (json.JSONDecodeError, TypeError):
            stored = recipe_data
        return {
            "id": existing.id,
            "recipe_title": existing.recipe_title,
            "recipe_data": stored if isinstance(stored, dict) else recipe_data,
        }

    favorite = Favorite(
        user_id=user_id,
        recipe_title=recipe_title,
        # Persist as JSON text in the TEXT column.
        recipe_data=json.dumps(recipe_data, ensure_ascii=False),
    )

    db.add(favorite)
    db.commit()
    db.refresh(favorite)

    # Return a plain dict — not the ORM object — so recipe_data is
    # already a parsed dict and FastAPI serialises it correctly without
    # needing from_attributes / ORM mode enabled on the response schema.
    return {
        "id": favorite.id,
        "recipe_title": favorite.recipe_title,
        "recipe_data": recipe_data,
    }


# ============================================================================
# LIST FAVORITES
# ============================================================================

def get_user_favorites(
    db: Session,
    user_id: int,
) -> list[dict]:
    """
    Return all favourites for a user, ordered by created_at descending.

    Corrupted JSON rows are silently skipped so one bad record cannot
    break the entire list endpoint.
    """
    favorites = (
        db.query(Favorite)
        .filter(Favorite.user_id == user_id)
        # Most recently saved first — Flutter renders top-of-list as newest.
        .order_by(Favorite.created_at.desc())
        .all()
    )

    result = []
    for fav in favorites:
        try:
            recipe_data = json.loads(fav.recipe_data)
        except (json.JSONDecodeError, TypeError):
            # Corrupted row — skip rather than crashing the whole list.
            continue

        result.append({
            "id": fav.id,
            "recipe_title": fav.recipe_title,
            "recipe_data": recipe_data,
        })

    return result


# ============================================================================
# DELETE FAVORITE
# ============================================================================

def delete_favorite(
    db: Session,
    favorite_id: int,
    user_id: int,
):
    """
    Delete a favourite by id, scoped to the owning user.

    Returns True when a row was deleted, False when not found or not owned
    by the given user_id (prevents cross-user deletion).
    """
    favorite = db.query(Favorite).filter(
        Favorite.id == favorite_id,
        Favorite.user_id == user_id,
    ).first()

    if not favorite:
        return False

    db.delete(favorite)
    db.commit()

    return True
