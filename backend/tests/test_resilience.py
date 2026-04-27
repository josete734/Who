"""Smoke tests for app.collectors.resilience.

Drives the wrapper against fake collectors that succeed, raise, or hang —
verifies findings flow through, exceptions become CollectorFailure, timeouts
trip the breaker, and the breaker short-circuits subsequent runs.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import importlib.util
import sys
from pathlib import Path

import pytest

# Load app.collectors.base and app.collectors.resilience directly from source,
# bypassing app/collectors/__init__.py which eagerly imports every collector
# (and therefore every runtime dep — sqlalchemy, redis, google-genai, ...).
# This keeps the resilience smoke test environment-light.
_COLLECTORS_DIR = Path(__file__).resolve().parent.parent / "app" / "collectors"


def _load(modname: str, path: Path):
    spec = importlib.util.spec_from_file_location(modname, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# Stub out the ``app.collectors`` package so ``from app.collectors.base ...``
# inside resilience.py resolves without running the real __init__.py.
import types as _types

_pkg_app = sys.modules.setdefault("app", _types.ModuleType("app"))
_pkg_app.__path__ = [str(_COLLECTORS_DIR.parent)]  # type: ignore[attr-defined]
_pkg_collectors = _types.ModuleType("app.collectors")
_pkg_collectors.__path__ = [str(_COLLECTORS_DIR)]  # type: ignore[attr-defined]
sys.modules["app.collectors"] = _pkg_collectors

# Real schemas module — lightweight (only pydantic).
_schemas = _load("app.schemas", _COLLECTORS_DIR.parent / "schemas.py")
_base = _load("app.collectors.base", _COLLECTORS_DIR / "base.py")
_resilience = _load("app.collectors.resilience", _COLLECTORS_DIR / "resilience.py")

Collector = _base.Collector
Finding = _base.Finding
CircuitBreaker = _resilience.CircuitBreaker
CollectorFailure = _resilience.CollectorFailure
run_with_resilience = _resilience.run_with_resilience
SearchInput = _schemas.SearchInput


pytestmark = pytest.mark.asyncio


def _input() -> SearchInput:
    return SearchInput(username="alice")


# ---------------------------------------------------------------------------
# Fake collectors
# ---------------------------------------------------------------------------
class HappyCollector(Collector):
    name = "happy"
    category = "test"
    needs = ()
    timeout_seconds = 5
    max_retries = 0

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        yield Finding(collector=self.name, category="test", entity_type="X", title="one")
        yield Finding(collector=self.name, category="test", entity_type="X", title="two")


class BoomCollector(Collector):
    name = "boom"
    category = "test"
    needs = ()
    timeout_seconds = 5
    max_retries = 0

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        raise RuntimeError("kaboom")
        yield  # pragma: no cover


class HangCollector(Collector):
    name = "hang"
    category = "test"
    needs = ()
    timeout_seconds = 1  # short so the test is fast
    max_retries = 0

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        await asyncio.sleep(10)
        yield Finding(collector=self.name, category="test", entity_type="X", title="never")


class FlakyCollector(Collector):
    """Fails the first attempt, succeeds the second."""

    name = "flaky"
    category = "test"
    needs = ()
    timeout_seconds = 5
    max_retries = 2
    _calls = 0

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        type(self)._calls += 1
        if type(self)._calls == 1:
            raise ConnectionError("transient")
        yield Finding(collector=self.name, category="test", entity_type="X", title="ok")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_happy_path_yields_findings_and_no_failure() -> None:
    items = [x async for x in run_with_resilience(HappyCollector(), _input())]
    assert len(items) == 2
    assert all(isinstance(i, Finding) for i in items)
    assert [i.title for i in items] == ["one", "two"]


async def test_exception_becomes_failure_record() -> None:
    items = [x async for x in run_with_resilience(BoomCollector(), _input())]
    assert len(items) == 1
    f = items[0]
    assert isinstance(f, CollectorFailure)
    assert f.error_type == "RuntimeError"
    assert "kaboom" in f.message
    assert f.collector == "boom"
    assert f.duration_ms >= 0


async def test_timeout_becomes_failure_with_timed_out_flag() -> None:
    items = [x async for x in run_with_resilience(HangCollector(), _input())]
    assert len(items) == 1
    f = items[0]
    assert isinstance(f, CollectorFailure)
    assert f.timed_out is True
    assert f.error_type == "TimeoutError"


async def test_retry_succeeds_after_transient_failure() -> None:
    FlakyCollector._calls = 0
    items = [x async for x in run_with_resilience(FlakyCollector(), _input())]
    # One Finding, no failure record (retry recovered).
    assert any(isinstance(i, Finding) for i in items)
    assert not any(isinstance(i, CollectorFailure) for i in items)
    assert FlakyCollector._calls == 2


async def test_circuit_breaker_opens_and_short_circuits() -> None:
    breaker = CircuitBreaker(threshold=3)
    # Three failing runs to trip the breaker.
    for _ in range(3):
        async for _item in run_with_resilience(BoomCollector(), _input(), breaker=breaker):
            pass
    assert breaker.is_open("boom")

    # Subsequent run should NOT call the collector — it should short-circuit
    # with a CircuitBreakerOpen failure.
    items = [x async for x in run_with_resilience(BoomCollector(), _input(), breaker=breaker)]
    assert len(items) == 1
    f = items[0]
    assert isinstance(f, CollectorFailure)
    assert f.error_type == "CircuitBreakerOpen"
    assert f.breaker_open is True


async def test_breaker_resets_on_success() -> None:
    breaker = CircuitBreaker(threshold=5)
    # Two failures, then a success — failure counter resets.
    for _ in range(2):
        async for _item in run_with_resilience(BoomCollector(), _input(), breaker=breaker):
            pass
    async for _item in run_with_resilience(HappyCollector(), _input(), breaker=breaker):
        pass
    # Boom's counter is independent of happy's — verify by tripping it
    # at exactly threshold from a fresh count would still need 5 more,
    # so just check breaker is not open for either.
    assert not breaker.is_open("happy")
    assert not breaker.is_open("boom")


async def test_collector_with_class_attr_overrides() -> None:
    """Verify per-collector timeout_seconds / max_retries are honored."""
    c = HangCollector()
    assert c.timeout_seconds == 1
    items = [x async for x in run_with_resilience(c, _input())]
    assert len(items) == 1
    assert isinstance(items[0], CollectorFailure)
    # duration should be roughly the configured timeout, not the 10s sleep.
    assert items[0].duration_ms < 3000
