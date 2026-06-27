"""
Authentication Dependency Layer

Provides a reusable FastAPI dependency that:
- Extracts the Bearer token from the Authorization header
- Validates and decodes it via core.security.decode_access_token
- Fetches and returns the authenticated User from the database

Used across all protected routes (billing, favorites, AI endpoints, etc.)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from database import get_db
from models import User
from core.security import decode_access_token

# ========================
# TOKEN EXTRACTION SCHEME
# ========================

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ========================
# CURRENT USER DEPENDENCY
# ========================

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Decode the JWT access token and return the authenticated user.

    Flow:
    1. Extract Bearer token from Authorization header
    2. Decode + validate via core.security.decode_access_token
    3. Read 'user_id' claim (standardised claim across the whole backend)
    4. Fetch user from DB and validate existence

    Raises HTTP 401 on any failure (invalid token, expired, user not found).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id: int | None = payload.get("user_id")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.get(User, user_id)
    if not user:
        raise credentials_exception

    return user
