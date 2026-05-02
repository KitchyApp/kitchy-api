"""
Authentication Service Layer

This module centralizes authentication logic:
- login
- refresh token rotation
- security enforcement

Why:
- keeps routers clean
- improves scalability and maintainability
"""

from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import User
from core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
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

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token({"user_id": user.id})

    refresh_token = create_refresh_token()

    # Store hashed version only
    user.refresh_token_hash = hash_token(refresh_token)

    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token
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