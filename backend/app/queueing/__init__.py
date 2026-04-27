"""Queueing utilities: retry policy + dead-letter queue (Wave 5/E4)."""
from __future__ import annotations

from app.queueing.retry import (
    PermanentError,
    TransientError,
    classify,
    compute_backoff,
    with_retry,
)
from app.queueing.dlq import DLQ_STREAM, drain, push, requeue

__all__ = [
    "PermanentError",
    "TransientError",
    "classify",
    "compute_backoff",
    "with_retry",
    "DLQ_STREAM",
    "drain",
    "push",
    "requeue",
]
