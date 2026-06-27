"""
Authentication Router

Handles:
- register
- login
- token refresh
- user status (plan + expiry)

Delegates all business logic to the service layer.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.auth import LoginSchema, RefreshSchema, RegisterSchema
from services.auth_service import get_user_status, login_user, refresh_tokens, register_user

router = APIRouter()


@router.post("/register")
def register(data: RegisterSchema, db: Session = Depends(get_db)):
    """Register a new user account."""
    return register_user(db, data.email, data.password)


@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    """
    Authenticate with email + password.

    Returns:
    - access_token  (short-lived JWT, 15 min)
    - refresh_token (opaque, 30 days, stored hashed in DB)
    """
    return login_user(db, data.email, data.password)


@router.post("/refresh")
def refresh(data: RefreshSchema, db: Session = Depends(get_db)):
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    Implements single-use rotation: the old refresh token is invalidated
    immediately after use.
    """
    return refresh_tokens(db, data.refresh_token)


@router.get("/user/status")
def user_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the authenticated user's current plan status.

    - Requires a valid JWT Bearer token.
    - Auto-downgrades expired premium subscriptions to free before responding.

    Response:
        is_premium  (bool)   — true if plan is active premium
        plan        (str)    — "free" | "premium"
        plan_expiry (str|null) — ISO-8601 datetime or null if no expiry set
    """
    return get_user_status(current_user, db)
