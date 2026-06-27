from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db

from dependencies.auth import get_current_user

from models.user import User

from schemas.favorite import (
    FavoriteCreate,
)

from services.favorite_service import (
    add_favorite,
    get_user_favorites,
    delete_favorite,
)

router = APIRouter()


# ============================================================================
# ADD FAVORITE
# ============================================================================

@router.post("/")
def create_favorite(
    data: FavoriteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return add_favorite(
        db=db,
        user_id=current_user.id,
        recipe_title=data.recipe_title,
        recipe_data=data.recipe_data,
    )


# ============================================================================
# LIST FAVORITES
# ============================================================================

@router.get("/")
def list_favorites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_user_favorites(
        db=db,
        user_id=current_user.id,
    )


# ============================================================================
# DELETE FAVORITE
# ============================================================================

@router.delete("/{favorite_id}")
def remove_favorite(
    favorite_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    success = delete_favorite(
        db=db,
        favorite_id=favorite_id,
        user_id=current_user.id,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Favorite not found",
        )

    return {
        "message": "Favorite removed",
    }
