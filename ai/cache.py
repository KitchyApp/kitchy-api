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
        Generates a unique cache key based on ingredients and language.

        Steps:
        - Sort ingredients for consistency
        - Combine with language
        - Hash using SHA-256 to produce fixed-length key

        This ensures:
        - Same inputs → same cache key
        - Avoids long/unsafe Redis keys
    """

    base = ",".join(sorted(ingredients)) + "_" + language
    return hashlib.sha256(base.encode()).hexdigest()


# ========================
# CACHE RETRIEVAL
# ========================

async def get_cached(key: str):
    """
       Retrieves cached data from Redis.

       Notes:
       - Async function (requires 'await' when called)
       - Returns parsed JSON if found
       - Returns None if cache miss
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
        Stores data in Redis cache with expiration (TTL).

        - Serializes value to JSON
        - Uses SETEX to apply expiration automatically
    """

    await redis_client.setex(
        key,
        TTL,
        json.dumps(value)
    )
