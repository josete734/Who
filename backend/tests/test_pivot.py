"""Tests for the auto-pivot cascade engine (Agent A8).

Covers:
  * extractor — synthetic findings yield expected pivot kinds.
  * policy   — depth gating and confidence floor.
  * dispatcher — dedupe against `case_pivots`, depth cap, budget cap,
    and that collectors are enqueued via the injected pool.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.pivot import extract
from app.pivot.dispatcher import maybe_dispatch
from app.pivot.extractor import Pivot
from app.pivot.policy import (
    DEFAULT_CONFIDENCE_FLOOR,
    allowed_at_depth,
    kind_to_search_field,
    passes_confidence_floor,
)


# --------------------------------------------------------------------------- #
# Extractor                                                                   #
# --------------------------------------------------------------------------- #
def _finding(**kw: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        title="",
        url=None,
        payload={},
        confidence=0.8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_extractor_pulls_structured_payload_keys():
    f = _finding(payload={
        "email": "Alice@Example.COM",
        "phone": "+34 600 111 222",
        "username": "@alice",
        "domain": "example.com",
        "full_name": "Alice Smith",
        "avatar_url": "https://cdn.example.com/a.png",
        "ip": "8.8.8.8",
    })
    pivots = extract(f)
    kinds = {(p.kind, p.value) for p in pivots}

    assert ("email", "alice@example.com") in kinds
    assert ("phone", "+34600111222") in kinds
    assert ("username", "alice") in kinds
    assert ("domain", "example.com") in kinds
    assert ("full_name", "Alice Smith") in kinds
    assert ("photo_url", "https://cdn.example.com/a.png") in kinds
    assert ("ip", "8.8.8.8") in kinds


def test_extractor_mines_free_text_and_url():
    f = _finding(
        title="Reach out at bob@acme.io or visit https://acme.io/bob",
        url="https://acme.io/bob",
        payload={"note": "BTC tip jar 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"},
    )
    pivots = {(p.kind, p.value) for p in extract(f)}
    assert ("email", "bob@acme.io") in pivots
    assert ("domain", "acme.io") in pivots
    assert ("username", "bob") in pivots
    assert ("crypto_address", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") in pivots


def test_extractor_ignores_blacklisted_social_domains():
    f = _finding(url="https://github.com/torvalds")
    pivots = {(p.kind, p.value) for p in extract(f)}
    assert ("domain", "github.com") not in pivots
    # but the username heuristic still fires
    assert ("username", "torvalds") in pivots


# --------------------------------------------------------------------------- #
# Policy                                                                      #
# --------------------------------------------------------------------------- #
def test_policy_depth_gating():
    assert allowed_at_depth("email", 0)
    assert allowed_at_depth("email", 2)
    # full_name only allowed at shallow depth
    assert allowed_at_depth("full_name", 1)
    assert not allowed_at_depth("full_name", 2)
    # negative depth always rejected
    assert not allowed_at_depth("email", -1)


def test_policy_confidence_floor():
    assert passes_confidence_floor(0.5)
    assert not passes_confidence_floor(0.39)
    assert passes_confidence_floor(DEFAULT_CONFIDENCE_FLOOR)


def test_policy_kind_to_field_mapping():
    assert kind_to_search_field("email") == "email"
    assert kind_to_search_field("photo_url") == "extra_context"
    assert kind_to_search_field("profile_id") == "username"


# --------------------------------------------------------------------------- #
# Dispatcher                                                                  #
# --------------------------------------------------------------------------- #
class _FakeRowResult:
    def __init__(self, rows: list[tuple] | None = None, scalar: Any = None, rowcount: int = 0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _FakeSession:
    """Minimal AsyncSession stub. Each test seeds the response queue."""

    def __init__(self, store: dict):
        self.store = store  # shared mutable state across sessions

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        params = params or {}
        if "select kind, value from case_pivots" in sql:
            return _FakeRowResult(rows=[
                (k, v) for (cid, k, v) in self.store["pivots"]
                if cid == params["cid"]
            ])
        if "select count(*) from collector_runs" in sql:
            return _FakeRowResult(scalar=self.store["budget_used"])
        if "select 1 from collector_runs" in sql:
            seen = self.store["collectors_already_run"].get(params["cid"], set())
            return _FakeRowResult(scalar=1 if params["name"] in seen else None)
        if "insert into case_pivots" in sql:
            key = (params["cid"], params["kind"], params["val"])
            if key in {(c, k, v) for (c, k, v) in self.store["pivots"]}:
                return _FakeRowResult(rowcount=0)
            self.store["pivots"].append(key)
            return _FakeRowResult(rowcount=1)
        if "update case_pivots set dispatched_at" in sql:
            self.store["dispatched"].append((params["cid"], params["kind"], params["val"]))
            return _FakeRowResult(rowcount=1)
        return _FakeRowResult()


class _FakePool:
    def __init__(self):
        self.jobs: list[tuple] = []
        self.closed = False

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, args, kwargs))

    async def close(self):
        self.closed = True


def _make_factories(store: dict, pool: _FakePool):
    @asynccontextmanager
    async def session_factory():
        yield _FakeSession(store)

    async def pool_factory():
        return pool

    return session_factory, pool_factory


@pytest.fixture
def store():
    return {
        "pivots": [],                 # list of (case_id_str, kind, value)
        "dispatched": [],
        "budget_used": 0,
        "collectors_already_run": {},  # case_id_str -> set[collector_name]
    }


async def test_dispatcher_inserts_and_enqueues(store):
    case_id = uuid.uuid4()
    pool = _FakePool()
    sf, pf = _make_factories(store, pool)

    pivots = [
        Pivot("email", "alice@example.com", source_finding_id=None, confidence=0.9),
        Pivot("domain", "example.com", source_finding_id=None, confidence=0.8),
    ]

    res = await maybe_dispatch(
        case_id, pivots, depth=0,
        session_factory=sf, pool_factory=pf,
    )

    assert res.inserted == 2
    assert res.enqueued >= 1               # at least one collector matches email/domain
    assert res.skipped_dedup == 0
    assert pool.closed is True
    assert all(j[0] == "run_case_task" for j in pool.jobs)
    assert len(store["dispatched"]) == res.enqueued and len(store["dispatched"]) > 0 \
        or res.enqueued == 0


async def test_dispatcher_dedupes_against_existing_pivots(store):
    case_id = uuid.uuid4()
    # Pre-seed an identical pivot.
    store["pivots"].append((str(case_id), "email", "alice@example.com"))

    pool = _FakePool()
    sf, pf = _make_factories(store, pool)

    res = await maybe_dispatch(
        case_id,
        [Pivot("email", "alice@example.com", None, 0.9)],
        depth=0,
        session_factory=sf, pool_factory=pf,
    )

    assert res.inserted == 0
    assert res.skipped_dedup == 1
    assert res.enqueued == 0
    assert pool.jobs == []


async def test_dispatcher_respects_depth_cap(store):
    case_id = uuid.uuid4()
    pool = _FakePool()
    sf, pf = _make_factories(store, pool)

    # depth=2 means new pivots would land at depth 3 — past max_pivot_depth=2.
    res = await maybe_dispatch(
        case_id,
        [Pivot("email", "x@y.com", None, 0.9)],
        depth=2,
        max_pivot_depth=2,
        session_factory=sf, pool_factory=pf,
    )
    assert res.inserted == 0
    assert res.enqueued == 0
    assert res.skipped_depth == 1


async def test_dispatcher_respects_confidence_floor(store):
    case_id = uuid.uuid4()
    pool = _FakePool()
    sf, pf = _make_factories(store, pool)

    res = await maybe_dispatch(
        case_id,
        [Pivot("email", "low@conf.com", None, 0.1)],
        depth=0,
        session_factory=sf, pool_factory=pf,
    )
    assert res.inserted == 0
    assert res.skipped_confidence == 1


async def test_dispatcher_respects_budget(store):
    case_id = uuid.uuid4()
    store["budget_used"] = 999  # already over budget

    pool = _FakePool()
    sf, pf = _make_factories(store, pool)

    res = await maybe_dispatch(
        case_id,
        [Pivot("email", "alice@example.com", None, 0.9)],
        depth=0,
        max_collectors_per_case=10,
        session_factory=sf, pool_factory=pf,
    )
    # Pivot is still recorded but no collectors are enqueued.
    assert res.inserted == 1
    assert res.enqueued == 0
    assert res.skipped_budget >= 1
    assert pool.jobs == []
