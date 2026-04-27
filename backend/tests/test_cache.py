"""Tests for app.cache using fakeredis."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from app import cache


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch):
    """Replace the module-level Redis client with a fakeredis instance."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _fake_client():
        return fake

    monkeypatch.setattr(cache, "_client", _fake_client)
    # Make sure the singleton from a prior test isn't reused.
    monkeypatch.setattr(cache, "_redis", None)
    yield fake


def test_make_key_is_stable_and_order_independent():
    k1 = cache.make_key("sherlock", {"username": "alice", "limit": 5})
    k2 = cache.make_key("sherlock", {"limit": 5, "username": "alice"})
    k3 = cache.make_key("sherlock", {"username": "bob", "limit": 5})
    assert k1 == k2
    assert k1 != k3
    assert k1.startswith("cache:sherlock:")


def test_make_key_handles_nested_structures():
    k1 = cache.make_key("x", {"a": [1, 2, {"b": 3}], "c": None})
    k2 = cache.make_key("x", {"c": None, "a": [1, 2, {"b": 3}]})
    assert k1 == k2


@pytest.mark.asyncio
async def test_set_get_roundtrip():
    key = cache.make_key("dns", {"domain": "example.com"})
    await cache.cache_set(key, {"ips": ["1.2.3.4"]}, ttl_seconds=60)
    got = await cache.cache_get(key)
    assert got == {"ips": ["1.2.3.4"]}


@pytest.mark.asyncio
async def test_get_miss_returns_none(_patch_redis):
    assert await cache.cache_get("cache:nope:abc") is None
    # miss counter should have ticked
    miss = await _patch_redis.get(f"{cache.STATS_PREFIX}:miss")
    assert int(miss) == 1


@pytest.mark.asyncio
async def test_hit_miss_counters(_patch_redis):
    key = cache.make_key("email", {"q": "foo@bar"})
    assert await cache.cache_get(key) is None  # miss
    await cache.cache_set(key, {"ok": True}, 60)
    assert await cache.cache_get(key) == {"ok": True}  # hit
    assert await cache.cache_get(key) == {"ok": True}  # hit

    hit = int(await _patch_redis.get(f"{cache.STATS_PREFIX}:hit"))
    miss = int(await _patch_redis.get(f"{cache.STATS_PREFIX}:miss"))
    hit_email = int(await _patch_redis.get(f"{cache.STATS_PREFIX}:hit:email"))
    assert hit == 2
    assert miss == 1
    assert hit_email == 2


@pytest.mark.asyncio
async def test_ttl_is_applied(_patch_redis):
    key = cache.make_key("search", {"q": "x"})
    await cache.cache_set(key, [1, 2, 3], ttl_seconds=42)
    ttl = await _patch_redis.ttl(key)
    assert 0 < ttl <= 42


@pytest.mark.asyncio
async def test_cache_set_skips_non_serializable(_patch_redis):
    key = cache.make_key("weird", {"q": "x"})

    class NotJSON:
        pass

    # Should not raise; should not write anything.
    await cache.cache_set(key, NotJSON(), ttl_seconds=60)
    assert await _patch_redis.get(key) is None


@pytest.mark.asyncio
async def test_with_cache_decorator_caches_calls():
    calls = {"n": 0}

    @cache.with_cache("mycoll", default_ttl=60)
    async def collect(query: dict) -> dict:
        calls["n"] += 1
        return {"got": query["q"]}

    a = await collect(query={"q": "alice"})
    b = await collect(query={"q": "alice"})
    c = await collect(query={"q": "bob"})

    assert a == b == {"got": "alice"}
    assert c == {"got": "bob"}
    assert calls["n"] == 2  # second alice call was cached


@pytest.mark.asyncio
async def test_scan_delete_prefix(_patch_redis):
    await cache.cache_set(cache.make_key("sherlock", {"u": "a"}), {"x": 1}, 60)
    await cache.cache_set(cache.make_key("sherlock", {"u": "b"}), {"x": 2}, 60)
    await cache.cache_set(cache.make_key("dns", {"d": "ex.com"}), {"x": 3}, 60)

    deleted = await cache.scan_delete_prefix("sherlock")
    assert deleted == 2

    # dns entry survives
    remaining = await _patch_redis.keys("cache:*")
    assert any(k.startswith("cache:dns:") for k in remaining)
    assert not any(k.startswith("cache:sherlock:") for k in remaining)


@pytest.mark.asyncio
async def test_get_stats_shape():
    key = cache.make_key("foo", {"a": 1})
    await cache.cache_get(key)  # miss
    await cache.cache_set(key, 1, 60)
    await cache.cache_get(key)  # hit

    stats = await cache.get_stats()
    assert stats.get("hit") == 1
    assert stats.get("miss") == 1
    assert stats.get("hit:foo") == 1
    assert stats.get("miss:foo") == 1


def test_default_ttls_table_has_required_categories():
    for cat in ("username", "email", "dns", "search", "gemini_websearch"):
        assert cat in cache.DEFAULT_TTLS
        assert cache.DEFAULT_TTLS[cat] > 0


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(test_set_get_roundtrip())
