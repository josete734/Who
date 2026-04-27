"""Auto-pivot cascade engine.

When a collector emits a Finding, the pivot engine extracts new atoms
(emails, phones, domains, usernames, ...) and dispatches additional
collectors targeted at those atoms — recursively, with safety knobs.

Public surface:
    - extract(finding)      -> list[Pivot]
    - maybe_dispatch(...)   -> coroutine
    - Pivot                 -> dataclass

See `dispatcher.py` for the WIRING block describing where the
orchestrator should plug the engine in.
"""
from __future__ import annotations

from app.pivot.dispatcher import Pivot, maybe_dispatch
from app.pivot.extractor import extract
from app.pivot.policy import (
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_MAX_COLLECTORS_PER_CASE,
    DEFAULT_MAX_PIVOT_DEPTH,
    PIVOT_KINDS,
    allowed_at_depth,
    kind_to_search_field,
)

__all__ = [
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_MAX_COLLECTORS_PER_CASE",
    "DEFAULT_MAX_PIVOT_DEPTH",
    "PIVOT_KINDS",
    "Pivot",
    "allowed_at_depth",
    "extract",
    "kind_to_search_field",
    "maybe_dispatch",
]
