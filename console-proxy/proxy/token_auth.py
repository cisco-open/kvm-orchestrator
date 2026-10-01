"""Token validation — reads single-use tokens from Redis."""

import json
import logging
import os

import redis.asyncio as redis

logger = logging.getLogger("console-proxy.auth")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_redis_pool = None


async def _get_redis():
    """Get or create Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_pool


async def validate_token(token: str) -> dict | None:
    """
    Validate a console token.
    Tokens are single-use: deleted from Redis after validation.
    Returns token metadata dict or None if invalid/expired.
    """
    r = await _get_redis()
    key = f"console_token:{token}"

    # Atomic get-and-delete
    pipe = r.pipeline()
    pipe.get(key)
    pipe.delete(key)
    results = await pipe.execute()

    data = results[0]
    if data is None:
        logger.warning("Token validation failed: not found or expired")
        return None

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.error("Token data is not valid JSON")
        return None
