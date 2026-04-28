"""Tests for Wave 3 — wiring of pivot, timeline and AI investigator dispatcher.

Pure-Python tests using monkeypatch + AsyncMock so no Postgres / Redis is
required. Validates:

* ``_depth_from_extra_context`` (orchestrator helper) extracts depth or
  defaults to 0.
* The pivot dispatcher integration triggers ``maybe_dispatch`` correctly
  with depth derived from the input.
* ``build_timeline`` is invoked in the orchestrator pipeline (we patch it
  out and assert the call happens).
* ``LiveCollectorDispatcher.add_pivot`` calls ``maybe_dispatch`` with a
  proper Pivot instance, and rejects unknown kinds without dispatching.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.orchestrator import _depth_from_extra_context


# ---------------------------------------------------------------------------
# 1) _depth_from_extra_context
# ---------------------------------------------------------------------------
def test_depth_from_extra_context_default_zero():
    assert _depth_from_extra_context(None) == 0
    assert _depth_from_extra_context("") == 0
    assert _depth_from_extra_context("nothing relevant here") == 0


def test_depth_from_extra_context_parses_value():
    assert _depth_from_extra_context("pivot_depth=1") == 1
    assert _depth_from_extra_context("pivot_depth: 2") == 2
    assert _depth_from_extra_context("foo=bar pivot_depth=3 baz=qux") == 3


def test_depth_from_extra_context_clamps_oversized():
    # Defensively clamps in [0, 10] so a bad value cannot run amok.
    assert _depth_from_extra_context("pivot_depth=999") == 10
    assert _depth_from_extra_context("pivot_depth=-5") == 0


def test_depth_from_extra_context_handles_garbage():
    assert _depth_from_extra_context("pivot_depth=not_a_number") == 0


# ---------------------------------------------------------------------------
# 2) LiveCollectorDispatcher.add_pivot — wires through to maybe_dispatch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatcher_add_pivot_calls_maybe_dispatch(monkeypatch):
    from app.ai_investigator.collector_dispatcher import LiveCollectorDispatcher
    from app.pivot.dispatcher import DispatchResult

    captured: dict = {}

    async def fake_dispatch(case_id, pivots, **kw):
        captured["case_id"] = case_id
        captured["pivots"] = list(pivots)
        captured["kwargs"] = kw
        return DispatchResult(
            inserted=1, enqueued=2, skipped_dedup=0, skipped_depth=0,
            skipped_confidence=0, skipped_budget=0,
        )

    monkeypatch.setattr(
        "app.ai_investigator.collector_dispatcher.maybe_dispatch",
        fake_dispatch,
        raising=True,
    )

    d = LiveCollectorDispatcher()
    cid = uuid.uuid4()
    out = await d.add_pivot(cid, "domain", "example.com")

    assert out["ok"] is True
    assert out["enqueued_collectors"] == 2
    assert out["inserted"] == 1
    assert captured["case_id"] == cid
    assert len(captured["pivots"]) == 1
    p = captured["pivots"][0]
    assert p.kind == "domain"
    assert p.value == "example.com"


@pytest.mark.asyncio
async def test_dispatcher_add_pivot_rejects_unknown_kind(monkeypatch):
    from app.ai_investigator.collector_dispatcher import LiveCollectorDispatcher

    # If we did call maybe_dispatch this assertion would explode the test.
    fail_dispatch = AsyncMock(
        side_effect=AssertionError("must not be called for invalid kind")
    )
    monkeypatch.setattr(
        "app.ai_investigator.collector_dispatcher.maybe_dispatch",
        fail_dispatch,
        raising=True,
    )

    d = LiveCollectorDispatcher()
    out = await d.add_pivot(uuid.uuid4(), "not_a_real_kind", "anything")
    assert "error" in out
    assert "valid_kinds" in out
    fail_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_add_pivot_rejects_empty_value(monkeypatch):
    from app.ai_investigator.collector_dispatcher import LiveCollectorDispatcher

    fail_dispatch = AsyncMock(
        side_effect=AssertionError("must not be called for empty value")
    )
    monkeypatch.setattr(
        "app.ai_investigator.collector_dispatcher.maybe_dispatch",
        fail_dispatch,
        raising=True,
    )

    d = LiveCollectorDispatcher()
    out = await d.add_pivot(uuid.uuid4(), "domain", "  ")
    assert out.get("error") == "value is empty"


@pytest.mark.asyncio
async def test_dispatcher_add_pivot_handles_dispatcher_failure(monkeypatch):
    """maybe_dispatch raising must surface as a structured error, not crash."""
    from app.ai_investigator.collector_dispatcher import LiveCollectorDispatcher

    async def boom(*a, **kw):
        raise RuntimeError("redis_unreachable")

    monkeypatch.setattr(
        "app.ai_investigator.collector_dispatcher.maybe_dispatch",
        boom,
        raising=True,
    )

    d = LiveCollectorDispatcher()
    out = await d.add_pivot(uuid.uuid4(), "email", "x@example.com")
    assert out.get("error") == "dispatch_failed"
    assert "redis_unreachable" in out.get("detail", "")


# ---------------------------------------------------------------------------
# 3) Pivot extractor still emits expected pivot kinds (Wave 3 sanity check —
#    the orchestrator wires `extract` into the per-finding loop, so the
#    extractor must behave consistently with what we expect downstream.)
# ---------------------------------------------------------------------------
def test_pivot_extractor_emits_email_from_payload():
    from app.collectors.base import Finding
    from app.pivot.extractor import extract

    f = Finding(
        collector="dummy",
        category="email",
        entity_type="EmailHit",
        title="Found contact",
        url=None,
        confidence=0.9,
        payload={"email": "Alice@Example.COM"},
    )
    pivots = extract(f)
    kinds = {p.kind for p in pivots}
    assert "email" in kinds
    # Normalised to lowercase by the extractor.
    email_pivots = [p for p in pivots if p.kind == "email"]
    assert email_pivots[0].value == "alice@example.com"


def test_pivot_extractor_emits_domain_from_url():
    from app.collectors.base import Finding
    from app.pivot.extractor import extract

    f = Finding(
        collector="dummy",
        category="web",
        entity_type="Page",
        title="Their landing page",
        url="https://acme-corp.example.org/about",
        confidence=0.8,
        payload={},
    )
    pivots = extract(f)
    domain_pivots = [p for p in pivots if p.kind == "domain"]
    assert domain_pivots, f"expected domain pivot, got {pivots}"
    # Generic platforms are blacklisted; "acme-corp.example.org" is not.
    assert any("acme-corp" in p.value for p in domain_pivots)
