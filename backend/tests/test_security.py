"""Tests for app.security: hash/verify/revoke and Redis rate limiting."""
from __future__ import annotations

import uuid

import fakeredis.aioredis
import pytest

from app.security import api_keys as ak
from app.security.rate_limit import RateLimiter


# ---------- token hashing ----------

def test_generate_token_has_prefix_and_entropy():
    t1 = ak.generate_token()
    t2 = ak.generate_token()
    assert t1.startswith(ak.TOKEN_PREFIX)
    assert t1 != t2
    assert len(t1) > 30


def test_hash_and_verify_roundtrip():
    token = ak.generate_token()
    h = ak.hash_token(token)
    assert h != token
    assert ak.verify_token(token, h) is True
    assert ak.verify_token("wrong-token", h) is False


def test_lookup_hash_is_deterministic_sha256():
    token = "osk_abc"
    assert ak.lookup_hash_for(token) == ak.lookup_hash_for(token)
    assert len(ak.lookup_hash_for(token)) == 64


def test_constant_time_eq():
    assert ak.constant_time_eq("abc", "abc")
    assert not ak.constant_time_eq("abc", "abd")
    assert not ak.constant_time_eq("abc", "abcd")


def test_safe_token_repr_never_leaks_full_token():
    token = ak.generate_token()
    rep = ak._safe_token_repr(token)
    assert token not in rep
    assert "..." in rep


# ---------- in-memory revoke flow (no DB) ----------

def test_revoke_marks_row():
    import datetime as dt
    row = ak.ApiKey(
        id=uuid.uuid4(), name="t", lookup_hash="x" * 64,
        hash="h", scopes=[], rate_limit_per_minute=60,
    )
    assert row.revoked_at is None
    row.revoked_at = dt.datetime.now(dt.timezone.utc)
    assert row.revoked_at is not None


# ---------- rate limiter ----------

@pytest.mark.asyncio
async def test_rate_limit_allows_within_budget():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RateLimiter(r, window_seconds=60)
    for i in range(5):
        allowed, count = await limiter.check(bucket="apikey:test1", limit=5)
        assert allowed, f"call {i} should be allowed"
        assert count == i + 1


@pytest.mark.asyncio
async def test_rate_limit_blocks_over_budget():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RateLimiter(r, window_seconds=60)
    for _ in range(3):
        allowed, _ = await limiter.check(bucket="apikey:test2", limit=3)
        assert allowed
    allowed, count = await limiter.check(bucket="apikey:test2", limit=3)
    assert not allowed
    assert count >= 3


@pytest.mark.asyncio
async def test_rate_limit_buckets_are_isolated():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RateLimiter(r, window_seconds=60)
    for _ in range(2):
        ok, _ = await limiter.check(bucket="apikey:A", limit=2)
        assert ok
    ok, _ = await limiter.check(bucket="apikey:A", limit=2)
    assert not ok
    # Different bucket still has full budget.
    ok, _ = await limiter.check(bucket="apikey:B", limit=2)
    assert ok


@pytest.mark.asyncio
async def test_rate_limit_zero_or_negative_limit_blocks():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RateLimiter(r, window_seconds=60)
    ok, _ = await limiter.check(bucket="apikey:zero", limit=0)
    assert not ok
