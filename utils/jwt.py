from datetime import datetime, timedelta
from jose import jwt
import os
from dotenv import load_dotenv

# ========================
# LOAD ENV
# ========================

load_dotenv()

# ========================
# JWT CONFIGURATION
# ========================

# Secret key used to sign JWT tokens
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY não definido no .env")

# Algorithm used for token signing
ALGORITHM = "HS256"

# Token expiration settings
ACCESS_EXPIRE_MINUTES = 15   # Short-lived token (API access)
REFRESH_EXPIRE_DAYS = 7  # Long-lived token (session renewal)


# ========================
# ACCESS TOKEN GENERATION
# ========================

def create_access_token(data: dict):
    """
        Generates a short-lived JWT access token.

        Purpose:
        - Used for authenticating API requests
        - Sent in Authorization header (Bearer token)

        Flow:
        1. Copy input payload
        2. Add expiration timestamp
        3. Encode using secret key

        Args:
            data: Dictionary containing user-related claims (e.g., user_id)

        Returns:
            Encoded JWT string
    """

    # Copy payload to avoid mutation
    to_encode = data.copy()

    # Define expiration time
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MINUTES)

    # Add expiration claim
    to_encode.update({"exp": expire})

    # Encode JWT token
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ========================
# REFRESH TOKEN GENERATION
# ========================

def create_refresh_token(data: dict):
    """
        Generates a long-lived JWT refresh token.

        Purpose:
        - Used to obtain new access tokens without re-login
        - Stored securely (usually DB + client storage)

        Flow:
        1. Copy payload
        2. Add longer expiration
        3. Encode token

        Security:
        - Should be validated against DB (token matching)
        - Must NOT be exposed unnecessarily

        Args:
            data: Dictionary containing user-related claims

        Returns:
            Encoded JWT string
    """

    # Copy payload
    to_encode = data.copy()

    # Define expiration time (longer than access token)
    expire = datetime.utcnow() + timedelta(days=REFRESH_EXPIRE_DAYS)

    # Add expiration claim
    to_encode.update({"exp": expire})

    # Encode JWT token
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
