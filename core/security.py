"""
Security Core Module

Single source of truth for all JWT and password operations.

Handles:
- SECRET_KEY loading from environment (JWT_SECRET)
- password hashing (bcrypt)
- access token creation + decoding
- refresh token generation (opaque, stored hashed)
- token hashing (SHA256)
"""

import hashlib
import os
import secrets
import warnings
from datetime import datetime, timedelta

from jose import jwt, JWTError
from passlib.context import CryptContext

# ========================
# SECRET KEY — ENV ONLY
# ========================

_secret = os.getenv("JWT_SECRET")

if not _secret:
    _secret = "dev-only-insecure-secret-CHANGE-IN-PRODUCTION"
    warnings.warn(
        "\n⚠️  AVISO DE SEGURANÇA: JWT_SECRET não está definido nas variáveis de ambiente.\n"
        "   A usar chave de desenvolvimento insegura.\n"
        "   NUNCA execute em produção sem definir JWT_SECRET=<chave aleatória longa>.\n",
        stacklevel=2,
    )

SECRET_KEY: str = _secret

# ========================
# JWT CONFIGURATION
# ========================

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30

# ========================
# PASSWORD HASHING
# ========================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash user password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify plain password against its bcrypt hash."""
    return pwd_context.verify(password, hashed)


# ========================
# ACCESS TOKEN (JWT)
# ========================

def create_access_token(data: dict) -> str:
    """
    Create a short-lived JWT access token.

    The payload must include 'user_id' as the identity claim.
    Expiration is controlled by ACCESS_TOKEN_EXPIRE_MINUTES.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.

    Returns the full payload dict on success.
    Raises jose.JWTError on invalid or expired tokens.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ========================
# REFRESH TOKEN (OPAQUE)
# ========================

def create_refresh_token() -> str:
    """
    Generate a cryptographically secure opaque refresh token.

    This is NOT a JWT — it is a random string stored hashed in the DB.
    Using an opaque token prevents payload tampering and keeps the
    refresh secret separate from the access token secret.
    """
    return secrets.token_urlsafe(64)


# ========================
# TOKEN HASHING (SHA-256)
# ========================

def hash_token(token: str) -> str:
    """
    Hash any token using SHA-256.

    Used for:
    - refresh tokens (stored hashed, never raw)
    - purchase tokens (anti-reuse check)
    """
    return hashlib.sha256(token.encode()).hexdigest()
