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

import bcrypt
from jose import jwt, JWTError

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
# Uses bcrypt directly instead of passlib to avoid the AttributeError
# "module 'bcrypt' has no attribute '__about__'" that appears with recent
# bcrypt versions (>=4.x) paired with older passlib releases.


def hash_password(password: str) -> str:
    """
    Hash a plaintext password with bcrypt.

    bcrypt.hashpw requires bytes input and returns bytes.
    We encode the password to UTF-8 before hashing and decode the result
    back to a UTF-8 string for storage in the database.
    """
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed_bytes.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.

    Both arguments are encoded to UTF-8 bytes before being passed to
    bcrypt.checkpw, which handles the constant-time comparison internally.
    """
    return bcrypt.checkpw(
        password.encode("utf-8"),
        hashed.encode("utf-8"),
    )


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
