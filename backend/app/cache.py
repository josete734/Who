"""Redis-backed cache layer for collector queries.

Goal: avoid repeating identical collector queries within a TTL window since
many collectors hit external rate-limited APIs.

Public API:
    - cache_get(key) -> value | None
    - cache_set(key, value, ttl_seconds) -> None
    - make_key(collector, query) -> str (stable hash)
    - with_cache(...) wrapper / cached_collector(...) decorator
    - DEFAULT_TTLS: dict of suggested TTLs per category

Stats are tracked under Redis keys ``cache:stats:hit`` / ``cache:stats:miss``
and ``cache:stats:hit:<collector>`` / ``cache:stats:miss:<collector>``.

Cached values are stored under ``cache:<collector>:<hash>``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Suggested default TTLs (in seconds) by collector category.
DEFAULT_TTLS: dict[str, int] = {
    "username": 24 * 3600,        # username enumeration
    "email": 6 * 3600,            # email / breach lookups
    "breach": 6 * 3600,
    "dns": 1 * 3600,              # dns / cert
    "cert": 1 * 3600,
    "search": 30 * 60,            # search / dorks
    "dorks": 30 * 60,
    "llm": 12 * 3600,             # LLM-grounded (gemini_websearch etc.)
    "gemini_websearch": 12 * 3600,
}

CACHE_PREFIX = "cache"
STATS_PREFIX = "cache:stats"

_redis: aioredis.Redis | None = None


async def _client() -> aioredis.Redis:
    """Lazy singleton Redis client. Reuses settings from app.config."""
    global _redis
    if _redis is None:
        s = get_settings()
        _redis = aioredis.from_url(s.redis_url, decode_responses=True)
    return _redis


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization for hashing query dicts."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def make_key(collector: str, query: dict) -> str:
    """Build a stable cache key for a (collector, query) pair.

    Uses blake2b over the canonical JSON of the query for collision resistance
    and short-ish keys.
    """
    canonical = _canonical_json(query)
    digest = hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()
    return f"{CACHE_PREFIX}:{collector}:{digest}"


async def cache_get(key: str) -> Any | None:
    """Return cached value or None. Tracks hit/miss counters in Redis."""
    try:
        r = await _client()
        raw = await r.get(key)
    except Exception:  # pragma: no cover - defensive
        logger.exception("cache_get failed for key=%s", key)
        return None

    collector = _collector_from_key(key)
    try:
        if raw is None:
            await _incr_stat("miss", collector)
            return None
        await _incr_stat("hit", collector)
        return json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("cache_get: corrupt JSON for key=%s, evicting", key)
        try:
            await r.delete(key)
        except Exception:  # pragma: no cover
            pass
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    """Store value under key for ttl_seconds. Skips silently if not JSON-serializable."""
    try:
        payload = json.dumps(value)
    except (TypeError, ValueError):
        logger.debug("cache_set: skipping non-JSON-serializable value for key=%s", key)
        return
    try:
        r = await _client()
        await r.set(key, payload, ex=max(1, int(ttl_seconds)))
    except Exception:  # pragma: no cover - defensive
        logger.exception("cache_set failed for key=%s", key)


async def _incr_stat(kind: str, collector: str | None) -> None:
    """Increment hit/miss counters. Best-effort; never raises."""
    try:
        r = await _client()
        async with r.pipeline(transaction=False) as p:
            p.incr(f"{STATS_PREFIX}:{kind}")
            if collector:
                p.incr(f"{STATS_PREFIX}:{kind}:{collector}")
            await p.execute()
    except Exception:  # pragma: no cover
        pass


def _collector_from_key(key: str) -> str | None:
    parts = key.split(":", 2)
    if len(parts) >= 2 and parts[0] == CACHE_PREFIX:
        return parts[1]
    return None


T = TypeVar("T")


def with_cache(
    collector_name: str,
    default_ttl: int,
    *,
    query_arg: str = "query",
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator factory. Wraps an async collector callable so identical
    ``query`` invocations within ``default_ttl`` seconds return cached results.

    The wrapped function is expected to be ``async def fn(..., query: dict, ...)``
    or accept the query as the first positional dict argument. Set ``query_arg``
    to the kwarg name that holds the query dict.

    Exposed but not wired into existing collectors -- integration is a
    later step (composes with the resilience layer).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            query = kwargs.get(query_arg)
            if query is None:
                # fall back to first positional dict
                for a in args:
                    if isinstance(a, dict):
                        query = a
                        break
            if not isinstance(query, dict):
                # Cannot build a stable key; just call through.
                return await fn(*args, **kwargs)

            key = make_key(collector_name, query)
            cached = await cache_get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            result = await fn(*args, **kwargs)
            await cache_set(key, result, default_ttl)
            return result

        return wrapper

    return decorator


# Alias for nicer ergonomics.
cached_collector = with_cache


def _ttl_for_category(category: str) -> int:
    """Resolve the TTL in seconds for a logical category.

    Falls back to 1 hour if the category is unknown.
    """
    return DEFAULT_TTLS.get(category, 3600)


def auto_cache(
    *,
    category: str,
    collector_name: str | None = None,
    query_arg: str = "query",
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Category-aware cache decorator.

    Looks up TTL from :data:`DEFAULT_TTLS` based on ``category`` (e.g.
    ``"dns"``, ``"username"``, ``"llm"``) and records hit/miss events into
    Prometheus via ``record_cache_event`` in addition to the Redis stats
    counters used by :func:`cache_get`.

    Available for future opt-in wiring; existing collectors are NOT
    modified by this change.

    Example::

        @auto_cache(category="dns", collector_name="dns_mx")
        async def fetch_mx(query: dict) -> list[dict]:
            ...
    """
    ttl = _ttl_for_category(category)

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        name = collector_name or fn.__name__

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            query = kwargs.get(query_arg)
            if query is None:
                for a in args:
                    if isinstance(a, dict):
                        query = a
                        break
            if not isinstance(query, dict):
                return await fn(*args, **kwargs)

            key = make_key(name, query)
            cached = await cache_get(key)
            try:
                from app.observability.metrics import record_cache_event
            except Exception:  # pragma: no cover - defensive
                def record_cache_event(*_a: Any, **_k: Any) -> None:  # type: ignore
                    return None

            if cached is not None:
                record_cache_event("hit", name)
                return cached  # type: ignore[return-value]
            record_cache_event("miss", name)
            result = await fn(*args, **kwargs)
            await cache_set(key, result, ttl)
            record_cache_event("set", name)
            return result

        # Expose introspectable attributes for tests / debugging.
        wrapper.__auto_cache_category__ = category  # type: ignore[attr-defined]
        wrapper.__auto_cache_ttl__ = ttl  # type: ignore[attr-defined]
        wrapper.__auto_cache_collector__ = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


async def scan_delete_prefix(prefix: str, *, batch: int = 500) -> int:
    """Delete all keys under ``cache:<prefix>:*`` using SCAN. Returns count deleted."""
    r = await _client()
    pattern = f"{CACHE_PREFIX}:{prefix}:*" if not prefix.startswith(CACHE_PREFIX) else f"{prefix}:*"
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match=pattern, count=batch)
        if keys:
            deleted += await r.delete(*keys)
        if cursor == 0:
            break
    return deleted


async def get_stats() -> dict[str, int]:
    """Return a flat dict of all ``cache:stats:*`` counters."""
    r = await _client()
    out: dict[str, int] = {}
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match=f"{STATS_PREFIX}:*", count=500)
        if keys:
            vals = await r.mget(keys)
            for k, v in zip(keys, vals, strict=False):
                short = k[len(STATS_PREFIX) + 1:]  # strip "cache:stats:"
                try:
                    out[short] = int(v) if v is not None else 0
                except (TypeError, ValueError):
                    out[short] = 0
        if cursor == 0:
            break
    return out
