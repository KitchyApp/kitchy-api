from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models.user import User
from schemas.favorite import FavoriteCreate, FavoriteResponse
from services.favorite_service import add_favorite, get_user_favorites, delete_favorite

router = APIRouter()


# ============================================================================
# ADD FAVORITE  →  POST /favorites
# ============================================================================
# Path is "" (not "/") so the full route becomes /favorites (no trailing slash),
# matching the Flutter client's apiClient.post('/favorites', ...) directly
# without relying on a 307 redirect that some HTTP stacks mishandle on POST.

@router.post("", response_model=FavoriteResponse, status_code=200)
def create_favorite(
    data: FavoriteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a generated recipe to the authenticated user's favourites.

    Accepts the full recipe payload (title + structured data) and persists
    it as a JSON blob in the favorites table.  Returns the new favourite's
    id so the Flutter client can later call DELETE /favorites/{id}.
    """
    return add_favorite(
        db=db,
        user_id=current_user.id,
        recipe_title=data.recipe_title,
        recipe_data=data.recipe_data,
    )


# ============================================================================
# LIST FAVORITES  →  GET /favorites
# ============================================================================

@router.get("", response_model=List[FavoriteResponse], status_code=200)
def list_favorites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all saved recipes for the authenticated user, newest first.

    Each item contains the favourite id (for deletion) and the full
    recipe_data dict so the Flutter client can reconstruct a Recipe object.
    """
    return get_user_favorites(
        db=db,
        user_id=current_user.id,
    )


# ============================================================================
# DELETE FAVORITE  →  DELETE /favorites/{favorite_id}
# ============================================================================

@router.delete("/{favorite_id}", status_code=200)
def remove_favorite(
    favorite_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a favourite by id.  Only the owner can delete their own entries
    (user_id filter inside delete_favorite prevents cross-user deletion).
    """
    success = delete_favorite(
        db=db,
        favorite_id=favorite_id,
        user_id=current_user.id,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Favorito não encontrado ou não pertence a este utilizador.",
        )

    return {"message": "Favorito removido com sucesso."}
