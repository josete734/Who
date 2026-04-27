"""Collector base class and registry.

A Collector takes a SearchInput and emits Findings asynchronously.
Each Collector self-registers on import via the @register decorator.
"""
from __future__ import annotations

import abc
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.schemas import SearchInput


@dataclass
class Finding:
    collector: str
    category: str
    entity_type: str
    title: str
    url: str | None = None
    confidence: float = 0.7
    payload: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable hash for dedup across collectors."""
        base = "|".join([self.category, self.entity_type, (self.url or self.title).lower()])
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


class Collector(abc.ABC):
    """Abstract collector. Subclasses declare `name`, `category`, `needs`, and implement `run`."""

    name: str = ""
    category: str = ""
    # Which SearchInput fields are required (any of them is enough unless `requires_all`)
    needs: tuple[str, ...] = ()
    requires_all: bool = False
    # Resilience knobs — consumed by app.collectors.resilience.run_with_resilience.
    # Subclasses may override any of these.
    timeout_seconds: int = 60
    max_retries: int = 1
    circuit_breaker_threshold: int = 5
    description: str = ""

    def applicable(self, input: SearchInput) -> bool:
        if not self.needs:
            return True
        available = set(input.non_empty_fields().keys())
        needs = set(self.needs)
        return needs.issubset(available) if self.requires_all else bool(available & needs)

    @abc.abstractmethod
    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:  # type: ignore[misc]
        """Async generator yielding Finding objects."""
        if False:
            yield  # pragma: no cover


class _Registry:
    def __init__(self) -> None:
        self._collectors: dict[str, type[Collector]] = {}

    def register(self, cls: type[Collector]) -> type[Collector]:
        if not cls.name:
            raise ValueError(f"Collector {cls.__name__} missing `name`")
        if cls.name in self._collectors:
            raise ValueError(f"Duplicate collector: {cls.name}")
        self._collectors[cls.name] = cls
        return cls

    def all(self) -> list[type[Collector]]:
        return list(self._collectors.values())

    def applicable_for(self, input: SearchInput) -> list[Collector]:
        instances = [c() for c in self._collectors.values()]
        return [c for c in instances if c.applicable(input)]

    def by_name(self, name: str) -> type[Collector] | None:
        return self._collectors.get(name)


collector_registry = _Registry()


def register(cls: type[Collector]) -> type[Collector]:
    return collector_registry.register(cls)


# ---------------------------------------------------------------------------
# ORCHESTRATOR WIRING — TODO for the integration agent (Wave 1 / A-wiring)
# ---------------------------------------------------------------------------
# The resilience wrapper lives in ``app.collectors.resilience``. To activate it
# the orchestrator must be changed from:
#
#     async for f in collector.run(search_input):
#         ...
#
# to something like:
#
#     from app.collectors.resilience import CircuitBreaker, run_with_resilience, CollectorFailure
#
#     breaker = CircuitBreaker(threshold=5)   # one per case
#     for collector in registry.applicable_for(search_input):
#         async for item in run_with_resilience(collector, search_input, breaker=breaker):
#             if isinstance(item, CollectorFailure):
#                 # persist as a per-collector status row, do NOT abort the case
#                 record_failure(case_id, item)
#             else:
#                 emit_finding(case_id, item)
#
# Per-collector overrides for ``timeout_seconds``, ``max_retries`` and
# ``circuit_breaker_threshold`` are picked up automatically from class attrs.
# ---------------------------------------------------------------------------
