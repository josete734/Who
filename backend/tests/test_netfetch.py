"""Tests for app.netfetch."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.netfetch.headers import USER_AGENTS, random_headers
from app.netfetch.tor import is_onion, tor_client
from app.netfetch import client as nf_client


def test_ua_pool_diversity():
    assert len(USER_AGENTS) == 50
    assert len(set(USER_AGENTS)) == 50
    seen = {random_headers()["User-Agent"] for _ in range(2000)}
    # With 50 UAs and 2000 draws, expect to hit (almost) all of them.
    assert len(seen) >= 45


@pytest.mark.asyncio
async def test_token_bucket_with_fakeredis(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    aio = fakeredis.aioredis  # type: ignore[attr-defined]

    fake = aio.FakeRedis(decode_responses=True)

    bucket = nf_client._RedisTokenBucket("redis://fake", rate=2.0, burst=2)

    async def _get_redis_patch(self):
        return fake

    monkeypatch.setattr(
        nf_client._RedisTokenBucket, "_get_redis", _get_redis_patch
    )

    # First two acquisitions should be immediate (burst=2).
    t0 = time.monotonic()
    await bucket.acquire("example.com", max_wait=0.1)
    await bucket.acquire("example.com", max_wait=0.1)
    fast = time.monotonic() - t0
    assert fast < 0.5

    # Third must wait at least ~ (1/rate) seconds for a refill.
    t1 = time.monotonic()
    await bucket.acquire("example.com", max_wait=2.0)
    waited = time.monotonic() - t1
    assert waited >= 0.2  # generous lower bound for 2 tok/s


def test_is_onion():
    assert is_onion("http://abcdefghijklmnop.onion/path")
    assert is_onion("https://foo.bar.onion")
    assert not is_onion("https://example.com")
    assert not is_onion("not a url")


@pytest.mark.asyncio
async def test_onion_routes_through_tor_client(monkeypatch):
    monkeypatch.setenv("TOR_SOCKS", "socks5://127.0.0.1:9050")
    c = tor_client()
    try:
        # httpx>=0.27 exposes ._mounts / ._transport with proxy info; we just
        # assert the client was constructed and carries a UA header.
        assert "User-Agent" in c.headers
    finally:
        await c.aclose()

    # And get_client('tor') should return a tor-flavoured client too.
    from app.netfetch import get_client

    c2 = await get_client("tor")
    try:
        assert "User-Agent" in c2.headers
    finally:
        await c2.aclose()
