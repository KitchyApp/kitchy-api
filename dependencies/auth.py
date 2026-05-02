"""
Authentication Dependency Layer

Provides reusable dependency to:
- Extract and validate JWT tokens
- Retrieve authenticated user

Used across protected routes (billing, user, etc.)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from database import get_db
from models import User
from core.security import SECRET_KEY, ALGORITHM

# ========================
# TOKEN EXTRACTION SCHEME
# ========================

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ========================
# CURRENT USER DEPENDENCY
# ========================

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """
    Extracts user from JWT access token.

    Flow:
    1. Decode JWT
    2. Extract user_id
    3. Fetch user from DB
    4. Validate existence

    Security:
    - Rejects invalid/expired tokens
    - Prevents unauthorized access
    """

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        user_id = payload.get("user_id")

        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    user = db.get(User, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    return user
