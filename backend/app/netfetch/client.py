"""Async httpx client wrapper with UA rotation, proxy pool, rate limiting,
backoff, and optional Tor routing.

Usage:
    from app.netfetch import get_client
    async with await get_client('gentle') as c:
        r = await c.get(url)
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Iterable, Literal, Optional
from urllib.parse import urlparse

import httpx

from .headers import USER_AGENTS, headers_for, random_headers
from .tor import is_onion, tor_client

HostPolicy = Literal["default", "gentle", "tor"]

_POLICY_RATES = {
    # tokens-per-second, burst
    "default": (5.0, 10),
    "gentle": (1.0, 2),
    "tor": (0.5, 2),
}


def _proxies_from_env() -> list[str]:
    raw = os.getenv("PROXIES", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


class _RedisTokenBucket:
    """Per-host token bucket backed by Redis. Falls back to in-process if Redis unreachable."""

    LUA = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local burst = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local data = redis.call('HMGET', key, 'tokens', 'ts')
    local tokens = tonumber(data[1])
    local ts = tonumber(data[2])
    if tokens == nil then tokens = burst end
    if ts == nil then ts = now end
    local delta = math.max(0, now - ts)
    tokens = math.min(burst, tokens + delta * rate)
    local allowed = 0
    if tokens >= 1 then
      tokens = tokens - 1
      allowed = 1
    end
    redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
    redis.call('EXPIRE', key, 300)
    return allowed
    """

    def __init__(self, redis_url: str, rate: float, burst: int):
        self.redis_url = redis_url
        self.rate = rate
        self.burst = burst
        self._redis = None
        self._local: dict[str, tuple[float, float]] = {}

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis  # type: ignore

            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
        except Exception:
            self._redis = False  # type: ignore
        return self._redis

    async def acquire(self, host: str, max_wait: float = 30.0) -> None:
        deadline = time.monotonic() + max_wait
        while True:
            ok = await self._try(host)
            if ok:
                return
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(1.0 / max(self.rate, 0.1))

    async def _try(self, host: str) -> bool:
        r = await self._get_redis()
        now = time.time()
        if r:
            try:
                allowed = await r.eval(
                    self.LUA, 1, f"netfetch:tb:{host}", self.rate, self.burst, now
                )
                return bool(int(allowed))
            except Exception:
                pass
        # local fallback
        tokens, ts = self._local.get(host, (float(self.burst), now))
        tokens = min(self.burst, tokens + max(0.0, now - ts) * self.rate)
        if tokens >= 1:
            self._local[host] = (tokens - 1, now)
            return True
        self._local[host] = (tokens, now)
        return False


class _Transport(httpx.AsyncBaseTransport):
    """Wrapping transport: rate limit, UA rotation, proxy rotation, 429/503 backoff."""

    # Wave 6 — dead-set cache TTL. We refresh from Redis at most this often
    # to avoid a round-trip on every request while still picking up fresh
    # health-check results within seconds.
    _DEAD_REFRESH_S = 30.0

    def __init__(
        self,
        proxies: list[str],
        bucket: _RedisTokenBucket,
        max_retries: int = 4,
        verify: bool = True,
    ):
        self._proxies = list(proxies)
        self._bucket = bucket
        self._max_retries = max_retries
        self._verify = verify
        self._proxy_idx = 0
        self._transports: dict[Optional[str], httpx.AsyncHTTPTransport] = {}
        self._dead_set: set[str] = set()
        self._dead_loaded_at: float = 0.0

    def _get_transport(self, proxy: Optional[str]) -> httpx.AsyncHTTPTransport:
        t = self._transports.get(proxy)
        if t is None:
            t = httpx.AsyncHTTPTransport(proxy=proxy, verify=self._verify, retries=0)
            self._transports[proxy] = t
        return t

    async def _refresh_dead_set(self) -> None:
        """Pull the proxy:dead Redis SET if our cached snapshot is stale.

        Fail-soft: if Redis is down we keep the previous snapshot (or empty
        if we never managed to load one). The proxy_health cron is the
        source of truth — see app.perf.proxy_health.
        """
        now = time.monotonic()
        if now - self._dead_loaded_at < self._DEAD_REFRESH_S:
            return
        self._dead_loaded_at = now
        try:
            redis = await self._bucket._get_redis()  # noqa: SLF001 — same module
            if redis is None:
                return
            from app.perf.proxy_health import REDIS_DEAD_SET_KEY
            members = await redis.smembers(REDIS_DEAD_SET_KEY)
            self._dead_set = {str(m) for m in members or []}
        except Exception:  # noqa: BLE001
            # Fail-open: keep the old snapshot, don't crash the request.
            return

    def _next_proxy(self) -> Optional[str]:
        if not self._proxies:
            return None
        # Filter against the (best-effort cached) dead-set. If every proxy
        # is currently marked dead, fall back to the original rotation so
        # we don't stall traffic — Redis may be lying or stale.
        live = [p for p in self._proxies if p not in self._dead_set]
        pool = live or self._proxies
        p = pool[self._proxy_idx % len(pool)]
        self._proxy_idx += 1
        return p

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        await self._bucket.acquire(host)
        # Wave 6 — keep our dead-proxy cache fresh so _next_proxy filters
        # correctly. Quick no-op when the cache hasn't expired yet.
        await self._refresh_dead_set()

        # Per-request UA rotation (override any default)
        ua = random.choice(USER_AGENTS)
        for k, v in headers_for(ua).items():
            request.headers[k] = v

        proxy = self._next_proxy()
        backoff = 1.0
        last_resp: Optional[httpx.Response] = None
        for attempt in range(self._max_retries + 1):
            transport = self._get_transport(proxy)
            try:
                resp = await transport.handle_async_request(request)
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
                if attempt >= self._max_retries:
                    raise
                proxy = self._next_proxy() or proxy
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            if resp.status_code in (429, 503) and attempt < self._max_retries:
                await resp.aread()
                ra = resp.headers.get("Retry-After")
                wait = backoff
                try:
                    if ra:
                        wait = max(wait, float(ra))
                except ValueError:
                    pass
                await asyncio.sleep(min(wait, 60.0))
                backoff = min(backoff * 2, 30.0)
                proxy = self._next_proxy() or proxy
                # rotate UA again
                ua = random.choice(USER_AGENTS)
                for k, v in headers_for(ua).items():
                    request.headers[k] = v
                last_resp = resp
                continue
            return resp
        assert last_resp is not None
        return last_resp

    async def aclose(self) -> None:
        for t in self._transports.values():
            await t.aclose()


async def get_client(host_policy: HostPolicy = "default") -> httpx.AsyncClient:
    """Return a configured httpx.AsyncClient for the requested host policy."""
    if host_policy == "tor":
        return tor_client()

    from app.config import get_settings

    settings = get_settings()
    rate, burst = _POLICY_RATES.get(host_policy, _POLICY_RATES["default"])
    bucket = _RedisTokenBucket(settings.redis_url, rate=rate, burst=burst)
    proxies = _proxies_from_env()

    transport = _Transport(proxies=proxies, bucket=bucket)
    timeout = httpx.Timeout(30.0, connect=15.0)
    client = httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        headers=random_headers(),
        follow_redirects=True,
    )

    # Auto-route .onion through Tor on per-request basis via event hook
    async def _onion_guard(request: httpx.Request):
        if is_onion(str(request.url)):
            raise httpx.RequestError(
                "Use get_client('tor') for .onion URLs", request=request
            )

    client.event_hooks["request"] = [_onion_guard]
    return client


__all__ = ["get_client", "is_onion"]
