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
):
    favorite = Favorite(
        user_id=user_id,
        recipe_title=recipe_title,
        recipe_data=json.dumps(recipe_data),
    )

    db.add(favorite)
    db.commit()
    db.refresh(favorite)

    return favorite


# ============================================================================
# LIST FAVORITES
# ============================================================================

def get_user_favorites(
    db: Session,
    user_id: int,
):
    favorites = db.query(Favorite).filter(
        Favorite.user_id == user_id
    ).all()

    result = []

    for fav in favorites:
        result.append({
            "id": fav.id,
            "recipe_title": fav.recipe_title,
            "recipe_data": json.loads(fav.recipe_data),
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
