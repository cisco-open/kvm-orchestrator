# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
