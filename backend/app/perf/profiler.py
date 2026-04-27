"""Lightweight phase profiler.

Provides a ``phase("name")`` context manager that records wall-clock
duration to the Prometheus ``case_collector_phase_seconds`` histogram and
emits a structured log entry.

Usage:

    from app.perf import phase

    with phase("collection"):
        await fan_out(...)

    async with async_phase("synthesis"):
        await synthesize(...)

The set of recommended phases is: ``collection``, ``entity_resolution``,
``triangulation``, ``synthesis``. The metric is labelled by ``phase`` so
custom names are also accepted.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

logger = logging.getLogger(__name__)


def _phase_histogram():
    """Lazily resolve the histogram, fail-soft if metrics module missing."""
    try:
        from app.observability.metrics import CASE_COLLECTOR_PHASE_SECONDS
        return CASE_COLLECTOR_PHASE_SECONDS
    except Exception:  # pragma: no cover - defensive
        return None


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Sync context manager: time the block and record duration."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        h = _phase_histogram()
        if h is not None:
            try:
                h.labels(phase=name).observe(elapsed)
            except Exception:  # pragma: no cover - defensive
                pass
        # Structured-style log line; works with stdlib + structlog.
        logger.info("phase.complete", extra={"phase": name, "duration_s": round(elapsed, 4)})


@asynccontextmanager
async def async_phase(name: str) -> AsyncIterator[None]:
    """Async-friendly variant of ``phase``."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        h = _phase_histogram()
        if h is not None:
            try:
                h.labels(phase=name).observe(elapsed)
            except Exception:  # pragma: no cover
                pass
        logger.info("phase.complete", extra={"phase": name, "duration_s": round(elapsed, 4)})
