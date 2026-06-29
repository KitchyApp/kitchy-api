"""
Authentication Service Layer

This module centralizes authentication logic:
- login
- refresh token rotation
- user status / subscription check
- security enforcement

Why:
- keeps routers clean
- improves scalability and maintainability
"""

from datetime import datetime

from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import User
from core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token
)


def login_user(db: Session, email: str, password: str):
    """
    Authenticate user and issue tokens.

    Flow:
    1. Validate credentials
    2. Generate access token (short-lived)
    3. Generate refresh token (long-lived)
    4. Store hashed refresh token

    Security:
    - Password verified via bcrypt
    - Refresh token stored hashed (never raw)
    """
    try:
        user = db.query(User).filter(User.email == email).first()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error during login: {exc}",
        ) from exc

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token({"user_id": user.id})
    refresh_token = create_refresh_token()

    try:
        user.refresh_token_hash = hash_token(refresh_token)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error saving session: {exc}",
        ) from exc

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def register_user(db: Session, email: str, password: str):
    """
    Register a new user account.

    Raises HTTP 400 if the email is already taken.
    Raises HTTP 500 (with a descriptive JSON body) on any database error,
    instead of letting an unhandled exception crash the uvicorn worker and
    produce an opaque traceback.
    """
    # --- check for existing account ---
    try:
        existing = db.query(User).filter(User.email == email).first()
    except Exception as exc:
        # Most likely cause: schema mismatch (a column in the model doesn't
        # exist in the physical table). run_column_migrations() in database.py
        # normally fixes this on startup, but we guard here too.
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=(
                f"Database error while checking for existing user: {exc}. "
                "The users table schema may be out of date — restart the server "
                "to trigger the automatic column migration."
            ),
        ) from exc

    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    # --- create new user ---
    try:
        user = User(
            email=email,
            password=hash_password(password),
        )
        db.add(user)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error while creating user: {exc}",
        ) from exc

    return {"message": "User created"}


def get_user_status(user: User, db: Session) -> dict:
    """
    Return the real premium status for the authenticated user.

    Logic (uses user.plan + user.plan_expiry stored at purchase time):
    - plan == "free"     → not premium, no expiry to check
    - plan == "premium"  → premium only if plan_expiry is None (manual/no-expiry)
                           or plan_expiry is still in the future
    - plan == "premium" but plan_expiry is in the past → auto-downgrade to free

    This keeps the query cheap (no extra Purchase table JOIN on every request)
    while still catching expired subscriptions automatically.
    """
    if user.plan == "premium" and user.plan_expiry is not None:
        if user.plan_expiry < datetime.utcnow():
            user.plan = "free"
            user.plan_expiry = None
            db.commit()

    is_premium = user.plan == "premium"

    return {
        "is_premium": is_premium,
        "plan": user.plan,
        "plan_expiry": user.plan_expiry.isoformat() if user.plan_expiry else None,
    }


def refresh_tokens(db: Session, refresh_token: str):
    """
    Refresh token rotation mechanism.

    Flow:
    1. Hash incoming token
    2. Validate against DB
    3. Generate new refresh token (rotation)
    4. Replace stored hash
    5. Issue new access token

    Security:
    - Prevents replay attacks
    - Invalidates old tokens automatically
    """

    token_hash = hash_token(refresh_token)

    user = db.query(User).filter(
        User.refresh_token_hash == token_hash
    ).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 🔥 ROTATION (CRITICAL)
    new_refresh = create_refresh_token()
    user.refresh_token_hash = hash_token(new_refresh)

    access_token = create_access_token({"user_id": user.id})

    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": new_refresh
    }
