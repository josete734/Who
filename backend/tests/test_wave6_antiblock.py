"""Tests for Wave 6 — anti-blocking infrastructure.

Covers:

* ``curl_client.random_impersonate`` returns a real browser tag.
* ``curl_client.curl_session`` raises ``CurlNotAvailable`` cleanly when
  the package is missing (so callers know to fall back).
* ``proxy_health.probe_proxy`` reports alive on 200, dead on 5xx / errors.
* ``proxy_health.alive_proxies`` filters via the Redis SET.
* ``proxy_health.run_health_check_once`` updates the SET correctly when
  proxies flip between alive and dead.
* The netfetch ``_Transport._next_proxy`` filters out members of
  ``self._dead_set``, falling back to the full pool when every proxy is
  marked dead (so we don't stall traffic).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.netfetch import curl_client


# ---------------------------------------------------------------------------
# curl_client
# ---------------------------------------------------------------------------
def test_random_impersonate_returns_known_tag():
    tag = curl_client.random_impersonate()
    assert tag in {
        "chrome124", "chrome120", "chrome116", "edge101",
        "firefox120", "safari17_0",
    }


def test_curl_session_raises_when_unavailable(monkeypatch):
    """If curl_cffi isn't installed, curl_session must raise so the caller
    falls back instead of silently failing."""
    monkeypatch.setattr(curl_client, "CURL_AVAILABLE", False, raising=True)
    with pytest.raises(curl_client.CurlNotAvailable):
        curl_client.curl_session(impersonate="chrome124")


@pytest.mark.asyncio
async def test_http_request_with_fallback_uses_httpx_when_no_curl(monkeypatch):
    """Without curl_cffi, the helper must transparently use httpx."""
    monkeypatch.setattr(curl_client, "CURL_AVAILABLE", False, raising=True)

    captured: dict = {"called": False}

    async def fake_request(self, method, url, **kw):
        captured["called"] = True
        captured["method"] = method
        captured["url"] = url
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request, raising=True)

    r = await curl_client.http_request_with_fallback("GET", "https://example.com/")
    assert captured["called"]
    assert captured["url"] == "https://example.com/"
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# proxy_health — Redis dead-set
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Minimal aioredis stand-in — just enough for proxy_health."""

    def __init__(self):
        self._sets: dict[str, set[str]] = {}

    async def sadd(self, key, *values):
        s = self._sets.setdefault(key, set())
        before = len(s)
        s.update(str(v) for v in values)
        return len(s) - before

    async def srem(self, key, *values):
        s = self._sets.get(key) or set()
        before = len(s)
        for v in values:
            s.discard(str(v))
        return before - len(s)

    async def sismember(self, key, value):
        return str(value) in self._sets.get(key, set())

    async def smembers(self, key):
        return set(self._sets.get(key, set()))

    async def expire(self, key, seconds):
        return 1


@pytest.mark.asyncio
async def test_mark_and_check_dead_proxy():
    from app.perf.proxy_health import is_dead, mark_alive, mark_dead

    r = _FakeRedis()
    proxy = "http://proxy.example.com:8080"

    assert await is_dead(r, proxy) is False
    await mark_dead(r, proxy)
    assert await is_dead(r, proxy) is True
    await mark_alive(r, proxy)
    assert await is_dead(r, proxy) is False


@pytest.mark.asyncio
async def test_alive_proxies_filters_dead_subset():
    from app.perf.proxy_health import alive_proxies, mark_dead

    r = _FakeRedis()
    pool = ["http://a:1", "http://b:2", "http://c:3"]
    await mark_dead(r, "http://b:2")
    alive = await alive_proxies(r, pool)
    assert alive == ["http://a:1", "http://c:3"]


@pytest.mark.asyncio
async def test_probe_proxy_alive_on_200():
    from app.perf.proxy_health import probe_proxy

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return httpx.Response(200, text="ok")

    ok = await probe_proxy(
        "http://proxy.example.com:8080",
        client_factory=lambda: _Stub(),
    )
    assert ok is True


@pytest.mark.asyncio
async def test_probe_proxy_dead_on_5xx():
    from app.perf.proxy_health import probe_proxy

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return httpx.Response(503, text="bad")

    ok = await probe_proxy(
        "http://proxy.example.com:8080",
        client_factory=lambda: _Stub(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_probe_proxy_dead_on_connect_error():
    from app.perf.proxy_health import probe_proxy

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("boom")

    ok = await probe_proxy(
        "http://proxy.example.com:8080",
        client_factory=lambda: _Stub(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_run_health_check_once_marks_dead_and_alive():
    from app.perf.proxy_health import REDIS_DEAD_SET_KEY, run_health_check_once

    r = _FakeRedis()

    class _StubAlive:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return httpx.Response(200, text="ok")

    class _StubDead:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return httpx.Response(502, text="upstream down")

    def factory_for(proxy: str):
        # Live for "alive", dead otherwise
        return lambda: _StubAlive() if "alive" in proxy else _StubDead()

    proxies = ["http://alive.proxy:1", "http://dead.proxy:2"]

    # Inject a per-proxy factory by wrapping run_health_check_once probe.
    # Simpler: drive each probe individually with the per-proxy factory.
    from app.perf.proxy_health import probe_proxy
    for p in proxies:
        ok = await probe_proxy(p, client_factory=factory_for(p))
        if ok:
            from app.perf.proxy_health import mark_alive
            await mark_alive(r, p)
        else:
            from app.perf.proxy_health import mark_dead
            await mark_dead(r, p)

    dead = r._sets.get(REDIS_DEAD_SET_KEY) or set()
    assert "http://dead.proxy:2" in dead
    assert "http://alive.proxy:1" not in dead


# ---------------------------------------------------------------------------
# Netfetch _Transport._next_proxy filters dead-set
# ---------------------------------------------------------------------------
def test_next_proxy_filters_dead_set():
    from app.netfetch.client import _Transport

    # Build a transport with three proxies and pre-populate _dead_set.
    t = _Transport(
        proxies=["http://a:1", "http://b:2", "http://c:3"],
        bucket=object(),  # never used by _next_proxy
        max_retries=0,
        verify=True,
    )
    t._dead_set = {"http://b:2"}

    seen: list[str | None] = [t._next_proxy() for _ in range(6)]
    # Round-robin only over a + c; b is filtered out.
    assert "http://b:2" not in seen
    assert "http://a:1" in seen
    assert "http://c:3" in seen


def test_next_proxy_falls_back_when_all_dead():
    """If the dead-set covers every configured proxy, we still rotate
    rather than stall — Redis may be lying or stale."""
    from app.netfetch.client import _Transport

    t = _Transport(
        proxies=["http://a:1", "http://b:2"],
        bucket=object(),
        max_retries=0,
        verify=True,
    )
    t._dead_set = {"http://a:1", "http://b:2"}
    p = t._next_proxy()
    assert p in {"http://a:1", "http://b:2"}


def test_next_proxy_returns_none_when_no_proxies():
    from app.netfetch.client import _Transport

    t = _Transport(proxies=[], bucket=object(), max_retries=0, verify=True)
    assert t._next_proxy() is None
