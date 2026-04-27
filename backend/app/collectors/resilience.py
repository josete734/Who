"""Resilience layer for collectors.

Provides:
- ``CollectorFailure``: structured failure record returned (not raised) when a
  collector explodes, times out, or is short-circuited by the breaker.
- ``CircuitBreaker``: per-case (or per-process) consecutive-failure tracker.
  After ``threshold`` consecutive failures a collector is skipped for the
  remainder of the case.
- ``run_with_resilience``: async generator wrapper around a ``Collector``'s
  ``run()`` that enforces a timeout, catches every exception, retries on
  transient failures with exponential backoff, and emits structured logs.

The orchestrator should drive collectors via ``run_with_resilience`` instead of
calling ``collector.run(input)`` directly (see TODO at the bottom of base.py).

This module deliberately has *zero* dependency on FastAPI / DB / Redis so it is
trivial to unit test.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.collectors.base import Collector, Finding
from app.schemas import SearchInput

logger = logging.getLogger("osint.collectors.resilience")


# ---------------------------------------------------------------------------
# Failure record
# ---------------------------------------------------------------------------
@dataclass
class CollectorFailure:
    """Structured failure record. Never raised — yielded as a sibling of Finding."""

    collector: str
    error_type: str
    message: str
    duration_ms: int
    attempt: int = 1
    timed_out: bool = False
    breaker_open: bool = False

    def as_log_extra(self) -> dict[str, Any]:
        # NB: avoid the key ``message`` — it collides with stdlib logging's
        # reserved LogRecord field and raises KeyError at log time.
        return {
            "collector": self.collector,
            "error_type": self.error_type,
            "error_message": self.message,
            "duration_ms": self.duration_ms,
            "attempt": self.attempt,
            "timed_out": self.timed_out,
            "breaker_open": self.breaker_open,
        }


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
@dataclass
class CircuitBreaker:
    """Trip after ``threshold`` consecutive failures. Scope is the lifetime of
    the instance — typically one per case."""

    threshold: int = 5
    _failures: dict[str, int] = field(default_factory=dict)
    _open: set[str] = field(default_factory=set)

    def is_open(self, collector_name: str) -> bool:
        return collector_name in self._open

    def record_success(self, collector_name: str) -> None:
        self._failures.pop(collector_name, None)

    def record_failure(self, collector_name: str) -> bool:
        """Record a failure; return True if the breaker just tripped open."""
        n = self._failures.get(collector_name, 0) + 1
        self._failures[collector_name] = n
        if n >= self.threshold and collector_name not in self._open:
            self._open.add(collector_name)
            logger.warning(
                "circuit breaker opened",
                extra={"collector": collector_name, "consecutive_failures": n},
            )
            return True
        return False


# ---------------------------------------------------------------------------
# Configurable defaults — collectors can override via class attributes.
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 1
DEFAULT_BREAKER_THRESHOLD = 5


def _get_attr(c: Collector, name: str, default: Any) -> Any:
    val = getattr(c, name, None)
    return default if val is None else val


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------
async def run_with_resilience(
    collector: Collector,
    input: SearchInput,
    *,
    breaker: CircuitBreaker | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
) -> AsyncIterator[Finding | CollectorFailure]:
    """Drive a collector resiliently.

    Yields each Finding as it arrives. On a failure (exception or timeout), it
    yields a single ``CollectorFailure`` and stops. Retries the *whole* run on
    timeout / network-ish errors up to ``max_retries`` extra times with
    exponential backoff (0.5s, 1s, 2s ...). Findings already yielded are NOT
    re-emitted on retry — once a finding is produced, we commit and continue.
    """
    name = collector.name or collector.__class__.__name__
    timeout = timeout_seconds if timeout_seconds is not None else _get_attr(
        collector, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
    )
    retries = max_retries if max_retries is not None else _get_attr(
        collector, "max_retries", DEFAULT_MAX_RETRIES
    )

    if breaker is not None and breaker.is_open(name):
        logger.info("collector skipped (breaker open)", extra={"collector": name})
        yield CollectorFailure(
            collector=name,
            error_type="CircuitBreakerOpen",
            message="circuit breaker open for this case",
            duration_ms=0,
            breaker_open=True,
        )
        return

    attempt = 0
    last_failure: CollectorFailure | None = None
    yielded_any = False

    while attempt <= retries:
        attempt += 1
        start = time.monotonic()
        try:
            agen = collector.run(input)
            # Wrap each ``__anext__`` in wait_for so a stuck iteration can't
            # outrun the overall timeout. We also bound total wall-time below.
            deadline = start + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError(f"collector exceeded {timeout}s wall clock")
                try:
                    item = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                yielded_any = True
                yield item
            # success
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "collector ok",
                extra={"collector": name, "duration_ms": duration_ms, "attempt": attempt},
            )
            if breaker is not None:
                breaker.record_success(name)
            return
        except asyncio.TimeoutError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            last_failure = CollectorFailure(
                collector=name,
                error_type="TimeoutError",
                message=str(e) or f"timeout after {timeout}s",
                duration_ms=duration_ms,
                attempt=attempt,
                timed_out=True,
            )
            logger.warning("collector timeout", extra=last_failure.as_log_extra())
        except asyncio.CancelledError:
            # Always propagate cancellation — never swallow.
            raise
        except BaseException as e:  # noqa: BLE001 — we genuinely catch all
            duration_ms = int((time.monotonic() - start) * 1000)
            last_failure = CollectorFailure(
                collector=name,
                error_type=type(e).__name__,
                message=str(e)[:500] or repr(e)[:500],
                duration_ms=duration_ms,
                attempt=attempt,
            )
            logger.exception("collector crashed", extra=last_failure.as_log_extra())

        # Retry decision — only if we haven't already yielded findings (avoid
        # double-emit) and we have retries left.
        if yielded_any or attempt > retries:
            break
        backoff = min(2 ** (attempt - 1) * 0.5, 5.0)
        await asyncio.sleep(backoff)

    if last_failure is not None:
        if breaker is not None:
            breaker.record_failure(name)
        yield last_failure
