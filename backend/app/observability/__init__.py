"""Observability package: Prometheus metrics + structured logs."""
from app.observability.metrics import (
    CACHE_EVENTS,
    CASE_ACTIVE,
    CASE_DURATION,
    COLLECTOR_DURATION,
    COLLECTOR_FINDINGS,
    COLLECTOR_RUNS,
    LLM_COST_USD,
    LLM_TOKENS,
    observe_collector,
    record_llm_call,
)
from app.observability.logging_setup import configure_logging, get_logger

__all__ = [
    "COLLECTOR_RUNS",
    "COLLECTOR_DURATION",
    "COLLECTOR_FINDINGS",
    "CACHE_EVENTS",
    "LLM_TOKENS",
    "LLM_COST_USD",
    "CASE_DURATION",
    "CASE_ACTIVE",
    "observe_collector",
    "record_llm_call",
    "configure_logging",
    "get_logger",
]
