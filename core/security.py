"""
Security Core Module

Handles:
- password hashing
- JWT creation
- refresh tokens
- token hashing

Centralized security logic for the entire backend
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

# ========================
# CONFIGURATION
# ========================

SECRET_KEY = "CHANGE_THIS_TO_ENV_VARIABLE_IN_PRODUCTION"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ========================
# PASSWORD HASHING
# ========================

def hash_password(password: str) -> str:
    """
    Hash user password using bcrypt.
    """
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify plain password against hashed version.
    """
    return pwd_context.verify(password, hashed)


# ========================
# ACCESS TOKEN (JWT)
# ========================

def create_access_token(data: dict):
    """
    Create short-lived access token.
    """
    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ========================
# REFRESH TOKEN
# ========================

def create_refresh_token():
    """
    Generate secure random refresh token.
    """
    return secrets.token_urlsafe(64)


# ========================
# HASH TOKEN (CRITICAL)
# ========================

def hash_token(token: str) -> str:
    """
    Hash token using SHA256 (used for refresh + purchase tokens).
    """
    return hashlib.sha256(token.encode()).hexdigest()
