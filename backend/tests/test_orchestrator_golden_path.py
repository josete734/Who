"""Golden-path integration test for the orchestrator wiring (Wave 10).

Doesn't bring up Postgres / Redis / Arq — instead it exercises the new
contract pieces that came out of Waves 3 + 9 in isolation:

* The pivot extractor + dispatcher cooperate end-to-end on a synthetic
  Finding (extract → maybe_dispatch → mock arq enqueue).
* The orchestrator's depth helper wires correctly into a `SearchInput`
  whose ``extra_context`` carries ``pivot_depth=N``.
* The Wave 9 consensus boost helper composes with the dedup logic.

Real DB / Redis / Arq paths are covered by ``pytest`` runs in the CI
workflow, where those services are spun up via ``docker compose``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.collectors.base import Finding
from app.orchestrator import _consensus_boost, _depth_from_extra_context
from app.pivot.dispatcher import DispatchResult, maybe_dispatch
from app.pivot.extractor import extract


# ---------------------------------------------------------------------------
# Pivot extract → dispatch — the headline cascade behavior of Wave 3.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pivot_extract_then_dispatch_with_email(monkeypatch):
    """A finding carrying an email payload must surface as a domain pivot
    (via the URL path of the contained address) and trigger collector
    enqueue when run through the dispatcher with mocked I/O."""
    # ``test_resilience.py`` may have stubbed app.collectors at collection
    # time; the dispatcher reads the registry to pick collectors per pivot
    # kind, and an empty stub registry would silently produce 0 enqueues.
    _force_real_collectors_module()
    f = Finding(
        collector="dummy",
        category="email",
        entity_type="EmailHit",
        title="Contacted at",
        url=None,
        confidence=0.9,
        payload={"email": "Alice@Example.COM"},
    )
    pivots = extract(f)
    kinds = {p.kind for p in pivots}
    assert "email" in kinds

    # Fake session_factory + pool_factory so the dispatcher can complete
    # without Postgres/Redis. We simulate a clean case (no existing pivots,
    # no prior collector runs) and watch what the dispatcher tries to do.
    enqueued: list[tuple] = []

    class _StubSession:
        async def execute(self, sql, params=None):
            text_sql = str(getattr(sql, "text", sql)).lower()

            class _R:
                def __init__(self, scalar=None, rows=()):
                    self._scalar = scalar
                    self._rows = list(rows)
                    self.rowcount = 1

                def scalar(self):
                    return self._scalar

                def all(self):
                    return self._rows

                def mappings(self):
                    return self

                def first(self):
                    return None

            if "select kind, value from case_pivots" in text_sql:
                return _R(rows=[])  # empty existing pivots
            if "count(*) from collector_runs" in text_sql:
                return _R(scalar=0)
            if "from collector_runs" in text_sql:
                return _R(scalar=None)  # _already_ran returns None
            if text_sql.startswith("insert into case_pivots"):
                return _R()
            if text_sql.startswith("update case_pivots"):
                return _R()
            return _R()

    class _StubCtx:
        async def __aenter__(self):
            return _StubSession()

        async def __aexit__(self, *exc):
            return False

    def session_factory():
        return _StubCtx()

    class _StubPool:
        async def enqueue_job(self, name, *args):
            enqueued.append((name, args))
            return None

        async def close(self):
            return None

    async def pool_factory():
        return _StubPool()

    cid = uuid.uuid4()
    result: DispatchResult = await maybe_dispatch(
        cid,
        pivots,
        depth=0,
        session_factory=session_factory,
        pool_factory=pool_factory,
    )

    assert result.inserted >= 1, f"expected at least one new pivot, got {result}"
    assert result.enqueued >= 1, f"expected at least one collector enqueued, got {result}"
    assert any(call[0] == "run_case_task" for call in enqueued)


@pytest.mark.asyncio
async def test_pivot_dispatch_respects_max_depth(monkeypatch):
    """Past max_pivot_depth, the dispatcher must short-circuit without
    touching the DB or Redis at all."""
    pivots = extract(
        Finding(
            collector="dummy",
            category="email",
            entity_type="EmailHit",
            title="x",
            url=None,
            confidence=0.9,
            payload={"email": "x@example.com"},
        )
    )

    session_factory = AsyncMock(side_effect=AssertionError("must not be called"))
    pool_factory = AsyncMock(side_effect=AssertionError("must not be called"))

    result = await maybe_dispatch(
        uuid.uuid4(),
        pivots,
        depth=5,  # next_depth = 6 > default cap of 2
        max_pivot_depth=2,
        session_factory=session_factory,
        pool_factory=pool_factory,
    )
    assert result.skipped_depth == len(pivots)
    assert result.inserted == 0
    assert result.enqueued == 0


# ---------------------------------------------------------------------------
# Helper composition — the small functions that the orchestrator wires
# into its hot path. They have to behave together correctly.
# ---------------------------------------------------------------------------
def test_depth_helper_feeds_into_dispatcher_contract():
    """The string the orchestrator writes into extra_context (when a sub-run
    is enqueued) must round-trip cleanly through the depth helper."""
    extra = "pivot_depth=2 inferred_home_lat=41.1 inferred_home_lon=1.1"
    assert _depth_from_extra_context(extra) == 2


def test_consensus_boost_with_realistic_collector_counts():
    """A username confirmed by Sherlock + WhatsMyName + Maigret should jump
    from the conservative 0.6 baseline to the auto-merge tier (≥0.8)."""
    base = 0.6  # Sherlock's documented baseline
    n_corroborating = 3
    boosted = _consensus_boost(base, n_corroborating)
    assert boosted >= 0.8


def test_consensus_boost_does_not_inflate_single_source():
    """A single Sherlock-only hit must keep its conservative 0.6 score."""
    assert _consensus_boost(0.6, 1) == 0.6


# ---------------------------------------------------------------------------
# Smoke: the registry contains every wave-introduced collector.
# ---------------------------------------------------------------------------
def _force_real_collectors_module():
    """Force a fresh import of the real ``app.collectors`` package.

    ``tests/test_resilience.py`` (pre-existing) replaces sys.modules with
    a stub that has no collectors, and pytest collects every test file
    before any test runs — meaning the stub may already be in place when
    these registry-shape tests execute. We discard the stub and re-import
    so the real registry is populated.
    """
    import importlib
    import sys
    import importlib.machinery

    stub = sys.modules.get("app.collectors")
    if stub is not None and not hasattr(stub, "ahmia"):
        # The real package always pulls every collector module via
        # ``app.collectors.__init__``; "ahmia" is one of them. If it's not
        # there, this is the stub — drop it and let importlib re-resolve.
        for k in list(sys.modules):
            if k == "app.collectors" or k.startswith("app.collectors."):
                del sys.modules[k]
        # Also drop the ``app`` shell that the stub installed so the real
        # package's ``__init__.py`` runs cleanly.
        if "app" in sys.modules and not hasattr(sys.modules["app"], "main"):
            del sys.modules["app"]
    return importlib.import_module("app.collectors")


def test_registry_contains_wave8_new_collectors():
    pkg = _force_real_collectors_module()
    registry = pkg.collector_registry

    names = {c.name for c in registry.all()}
    expected_new = {
        "youtube",
        "rdap",
        "passive_dns",
        "security_headers",
        "huggingface",
        "infosubvenciones",
        "transparencia_es",
    }
    missing = expected_new - names
    assert missing == set(), f"missing wave-8 collectors: {missing}"


def test_registry_no_rapidapi_collectors():
    """Wave 1 removed every rapidapi_* collector. The registry must stay
    free of them."""
    pkg = _force_real_collectors_module()
    registry = pkg.collector_registry

    names = [c.name for c in registry.all()]
    assert all(not n.startswith("rapidapi_") for n in names), names
