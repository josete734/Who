"""Tests for app.queueing (retry policy + DLQ) using fakeredis."""
from __future__ import annotations

import asyncio

import pytest

from app.queueing import dlq, retry
from app.queueing.retry import (
    MAX_TRIES,
    PermanentError,
    TransientError,
    classify,
    compute_backoff,
    with_retry,
)


@pytest.fixture
async def fake_redis():
    fakeredis = pytest.importorskip("fakeredis")
    # fakeredis.aioredis exposes an asyncio Redis client.
    try:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    except AttributeError:  # newer fakeredis
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
    dlq.set_client(client)
    try:
        yield client
    finally:
        dlq.set_client(None)
        try:
            await client.aclose()
        except Exception:
            pass


def test_classify_transient_and_permanent():
    assert classify(TransientError("x")) == "transient"
    assert classify(PermanentError("x")) == "permanent"
    assert classify(asyncio.TimeoutError()) == "transient"
    assert classify(ConnectionError("nope")) == "transient"
    assert classify(ValueError("bad input")) == "permanent"


def test_classify_http_status():
    class E(Exception):
        status_code = 503

    class E4(Exception):
        status_code = 404

    assert classify(E()) == "transient"
    assert classify(E4()) == "permanent"


def test_compute_backoff_caps():
    assert compute_backoff(1) == 2.0
    assert compute_backoff(2) == 4.0
    assert compute_backoff(3) == 8.0
    # capped at 5min (300s)
    assert compute_backoff(20) == 300.0


async def test_dlq_push_drain_requeue_delete(fake_redis):
    sid = await dlq.push({"task": "t", "error": "boom"})
    assert sid

    entries = await dlq.drain(max=10)
    assert len(entries) == 1
    assert entries[0]["fields"]["task"] == "t"
    assert entries[0]["fields"]["error"] == "boom"

    res = await dlq.requeue(entries[0]["id"])
    assert res is not None
    assert res["fields"]["requeued_from"] == entries[0]["id"]

    entries2 = await dlq.drain(max=10)
    assert len(entries2) == 1
    assert entries2[0]["id"] != entries[0]["id"]

    n = await dlq.delete(entries2[0]["id"])
    assert n == 1
    assert await dlq.drain(max=10) == []


async def test_requeue_missing_returns_none(fake_redis):
    assert await dlq.requeue("0-0") is None


async def test_with_retry_permanent_goes_to_dlq(fake_redis):
    @with_retry
    async def task(ctx, x):
        raise PermanentError("bad")

    with pytest.raises(PermanentError):
        await task({"job_try": 1, "job_id": "j1"}, 42)

    entries = await dlq.drain(max=10)
    assert len(entries) == 1
    f = entries[0]["fields"]
    assert f["task"] == "task"
    assert f["kind"] == "permanent"
    assert f["job_id"] == "j1"


async def test_with_retry_transient_exhausted_goes_to_dlq(fake_redis):
    @with_retry
    async def task(ctx):
        raise TransientError("flap")

    # Simulate Arq calling on the final attempt.
    with pytest.raises(TransientError):
        await task({"job_try": MAX_TRIES, "job_id": "j2"})

    entries = await dlq.drain(max=10)
    assert len(entries) == 1
    assert entries[0]["fields"]["kind"] == "transient"


async def test_with_retry_transient_signals_retry(fake_redis):
    @with_retry
    async def task(ctx):
        raise TransientError("flap")

    # On a non-final attempt, either Arq's Retry is raised (if installed)
    # or the underlying TransientError. Either way, nothing should hit DLQ.
    try:
        from arq.jobs import Retry  # type: ignore
        expected = (Retry, TransientError)
    except ImportError:
        expected = (TransientError,)

    with pytest.raises(expected):
        await task({"job_try": 1, "job_id": "j3"})

    assert await dlq.drain(max=10) == []


async def test_with_retry_success_passthrough(fake_redis):
    @with_retry
    async def task(ctx, x):
        return x * 2

    assert await task({"job_try": 1}, 21) == 42
    assert await dlq.drain(max=10) == []
