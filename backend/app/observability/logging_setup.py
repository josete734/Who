"""Structured JSON logging via structlog.

Emits one JSON object per line on stdout with keys:
    ts, level, event, case_id, collector, latency_ms, error
plus any additional context bound via ``logger.bind(...)``.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def _ensure_keys(_: Any, __: str, event_dict: dict) -> dict:
    """Guarantee canonical keys are present (None when not bound)."""
    for key in ("case_id", "collector", "latency_ms", "error"):
        event_dict.setdefault(key, None)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit JSON lines on stdout."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _ensure_keys,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name) if name else structlog.get_logger()


# WIRING (backend/app/main.py, near app startup):
#   from app.observability.logging_setup import configure_logging
#   configure_logging(level="INFO")
