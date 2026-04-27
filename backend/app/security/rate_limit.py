"""Sliding-window rate limiter backed by Redis.

Algorithm: ZSET per key, scored by epoch-ms. On each request:
  1. ZREMRANGEBYSCORE to drop entries older than the window
  2. ZCARD to count
  3. If count >= limit: reject
  4. ZADD <now>; PEXPIRE <window>

Key namespace: "rl:apikey:<id>:<window_seconds>"
"""
from __future__ import annotations

import time
import uuid

import redis.asyncio as redis_async
from fastapi import HTTPException, Request, status

from app.config import get_settings


class RateLimiter:
    def __init__(self, redis_client: redis_async.Redis, *, window_seconds: int = 60):
        self.redis = redis_client
        self.window_seconds = window_seconds

    async def check(self, *, bucket: str, limit: int) -> tuple[bool, int]:
        """Return (allowed, current_count_after)."""
        if limit <= 0:
            return False, 0
        now_ms = int(time.time() * 1000)
        window_ms = self.window_seconds * 1000
        cutoff = now_ms - window_ms
        key = f"rl:{bucket}:{self.window_seconds}"

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        _, current = await pipe.execute()

        if current >= limit:
            return False, current

        pipe = self.redis.pipeline()
        # Unique member so concurrent calls don't collide on the same score
        pipe.zadd(key, {f"{now_ms}:{uuid.uuid4().hex}": now_ms})
        pipe.pexpire(key, window_ms)
        await pipe.execute()
        return True, current + 1


_redis_singleton: redis_async.Redis | None = None


def get_redis() -> redis_async.Redis:
    global _redis_singleton
    if _redis_singleton is None:
        _redis_singleton = redis_async.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis_singleton


def set_redis_for_tests(client: redis_async.Redis) -> None:
    """Inject a fakeredis client for tests."""
    global _redis_singleton
    _redis_singleton = client


async def rate_limit_dependency(request: Request) -> None:
    """Apply per-API-key rate limit. Requires require_api_key to have run first."""
    api_key = getattr(request.state, "api_key", None)
    if api_key is None:
        return
    limiter = RateLimiter(get_redis(), window_seconds=60)
    allowed, _ = await limiter.check(
        bucket=f"apikey:{api_key.id}", limit=api_key.rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )
