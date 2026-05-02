"""
Authentication Router

Handles:
- login
- token refresh

Delegates logic to service layer (best practice)
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from schemas.auth import LoginSchema, RefreshSchema
from services.auth_service import login_user, refresh_tokens

router = APIRouter()


@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    """
    User login endpoint.

    Returns:
    - access_token
    - refresh_token
    """
    return login_user(db, data.email, data.password)


@router.post("/refresh")
def refresh(data: RefreshSchema, db: Session = Depends(get_db)):
    """
    Token refresh endpoint.

    Implements secure rotation.
    """
    return refresh_tokens(db, data.refresh_token)