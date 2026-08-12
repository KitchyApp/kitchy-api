"""Redis-backed async cache helpers for AI recipe responses."""

# Hashing utility for cache key generation
import hashlib

# JSON serialization/deserialization
import json

# Redis client
import redis.asyncio as redis

# Environment variables
import os

# ========================
# REDIS CONFIGURATION
# ========================

# Read Redis connection URL from environment
redis_url = os.getenv("REDIS_URL")

# Initialize Redis client
if redis_url:
    # Production environment (e.g., Render, AWS, etc.)
    redis_client = redis.from_url(redis_url, decode_responses=True)
else:
    # Local development (default Redis instance)
    redis_client = redis.Redis(
        host="localhost",
        port=6379,
        decode_responses=True
    )

# ========================
# CACHE SETTINGS
# ========================

# Time-to-live for cache entries (24 hours)
TTL = 60 * 60 * 24  # 24h


# ========================
# CACHE KEY GENERATION
# ========================

def generate_cache_key(ingredients: list[str], language: str):
    """
    Build a deterministic Redis key from sorted ingredients and locale.

    Steps:
    - Sort ingredients for consistency
    - Combine with language
    - Hash using SHA-256 to produce fixed-length key

    Same inputs always produce the same key, keeping Redis keys short and safe.
    """

    base = ",".join(sorted(ingredients)) + "_" + language
    return hashlib.sha256(base.encode()).hexdigest()


# ========================
# CACHE RETRIEVAL
# ========================

async def get_cached(key: str):
    """
    Fetch a cached value from Redis by key.

    Returns parsed JSON on a hit, or None on a miss. Must be awaited.
    """

    data = await redis_client.get(key)

    if data:
        return json.loads(data)

    return None


# ========================
# CACHE STORAGE
# ========================

async def set_cache(key: str, value: dict):
    """
    Store a JSON-serialisable dict in Redis with the module TTL.

    Uses SETEX so entries expire automatically after TTL seconds.
    """

    await redis_client.setex(
        key,
        TTL,
        json.dumps(value)
    )
