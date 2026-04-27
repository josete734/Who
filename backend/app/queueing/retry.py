"""Retry wrapper for Arq tasks.

Classifies failures into transient vs permanent. Transient errors are
retried with exponential backoff (base 2s, capped at 5min, max 4 tries).
Permanent failures are routed to the Redis-stream DLQ.

Use as a decorator on Arq task functions::

    @with_retry
    async def my_task(ctx, *args, **kwargs):
        ...

The wrapper expects an Arq-style ``ctx`` first argument exposing
``job_try`` (1-based attempt counter). When ``ctx`` is missing or lacks
the field, the wrapper falls back to a single attempt + DLQ on failure.
"""
from __future__ import annotations

import asyncio
import functools
import json
from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

# --- public knobs -----------------------------------------------------------
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 300.0  # 5 minutes
MAX_TRIES = 4


class TransientError(Exception):
    """Marker: should be retried with backoff."""


class PermanentError(Exception):
    """Marker: should NOT be retried; goes to DLQ."""


# Exception classes commonly considered transient. Strings to avoid hard deps.
_TRANSIENT_NAMES = {
    "TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionRefusedError",
    "ClientConnectorError",
    "ServerDisconnectedError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteDisconnected",
    "TemporaryFailure",
}


def classify(exc: BaseException) -> str:
    """Return ``"transient"`` or ``"permanent"`` for *exc*."""
    if isinstance(exc, TransientError):
        return "transient"
    if isinstance(exc, PermanentError):
        return "permanent"
    if isinstance(exc, asyncio.TimeoutError):
        return "transient"
    if isinstance(exc, OSError):  # covers ConnectionError, etc.
        return "transient"
    name = type(exc).__name__
    if name in _TRANSIENT_NAMES:
        return "transient"
    # HTTP-ish: 5xx transient, 4xx permanent (best-effort duck typing).
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        if 500 <= status < 600:
            return "transient"
        if 400 <= status < 500:
            return "permanent"
    return "permanent"


def compute_backoff(attempt: int) -> float:
    """Exponential backoff: base * 2**(attempt-1), capped at max."""
    if attempt < 1:
        attempt = 1
    delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return float(min(delay, MAX_BACKOFF_SECONDS))


def _job_try(ctx: Any) -> int:
    if isinstance(ctx, dict):
        return int(ctx.get("job_try") or 1)
    return int(getattr(ctx, "job_try", 1) or 1)


def _job_id(ctx: Any) -> str | None:
    if isinstance(ctx, dict):
        return ctx.get("job_id")
    return getattr(ctx, "job_id", None)


def with_retry(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Decorate an Arq async task with retry + DLQ behavior."""

    @functools.wraps(func)
    async def wrapper(ctx: Any, *args: Any, **kwargs: Any) -> Any:
        attempt = _job_try(ctx)
        try:
            return await func(ctx, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            kind = classify(exc)
            job_id = _job_id(ctx)
            log.warning(
                "queueing.task_failed",
                task=getattr(func, "__name__", "?"),
                job_id=job_id,
                attempt=attempt,
                max_tries=MAX_TRIES,
                kind=kind,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            if kind == "transient" and attempt < MAX_TRIES:
                # Tell Arq to retry: raise its Retry if available, else re-raise.
                try:
                    from arq.jobs import Retry  # type: ignore

                    raise Retry(defer=compute_backoff(attempt))
                except ImportError:
                    raise
            # permanent OR exhausted -> DLQ
            try:
                from app.queueing.dlq import push as dlq_push

                await dlq_push(
                    {
                        "task": getattr(func, "__name__", "?"),
                        "job_id": job_id or "",
                        "attempt": str(attempt),
                        "kind": kind,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "args": json.dumps(list(args), default=str),
                        "kwargs": json.dumps(kwargs, default=str),
                    }
                )
            except Exception as dlq_exc:  # noqa: BLE001
                log.error(
                    "queueing.dlq_push_failed",
                    job_id=job_id,
                    error=str(dlq_exc),
                )
            log.error(
                "queueing.task_dead_lettered",
                task=getattr(func, "__name__", "?"),
                job_id=job_id,
                kind=kind,
            )
            raise

    return wrapper
