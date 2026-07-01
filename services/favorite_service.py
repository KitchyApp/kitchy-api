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
    favorite = db.query(Favorite).filter(
        Favorite.id == favorite_id,
        Favorite.user_id == user_id,
    ).first()

    if not favorite:
        return False

    db.delete(favorite)
    db.commit()

    return True
