"""Async rule evaluator.

Loads enabled rules from the ``rules`` table and applies the DSL to a
given event. Designed to be called from event-bus consumers; the caller
is responsible for persisting any returned :class:`Alert` rows into the
``alerts`` table.

The DB parameter is intentionally typed loosely (``Any``) so the engine
can be exercised with an in-memory list-of-rules stub in tests::

    class StubDB:
        def __init__(self, rules): self.rules = rules
        async def load_rules(self): return self.rules
"""
from __future__ import annotations

import logging
from typing import Any

from app.rules.dsl import Alert, Rule, evaluate_rule

logger = logging.getLogger(__name__)


async def _load_rules_from_db(db: Any) -> list[Rule]:
    """Best-effort loader.

    Accepts either:
      * a SQLAlchemy ``AsyncSession`` (executes a SELECT),
      * an object exposing ``async load_rules() -> list[Rule|dict]``,
      * a plain iterable of :class:`Rule` / dicts (for tests).
    """
    # Plain iterable / list provided directly.
    if isinstance(db, list):
        return [r if isinstance(r, Rule) else Rule(**r) for r in db]

    # Stub/mock with custom loader.
    loader = getattr(db, "load_rules", None)
    if callable(loader):
        rows = await loader()
        return [r if isinstance(r, Rule) else Rule(**r) for r in rows]

    # Real SQLAlchemy session.
    execute = getattr(db, "execute", None)
    if callable(execute):
        try:
            from sqlalchemy import text  # local import: keeps tests dep-free

            result = await db.execute(
                text("SELECT id, name, dsl, enabled FROM rules WHERE enabled = TRUE")
            )
            rows = result.mappings().all()
            return [
                Rule(
                    id=str(row["id"]) if row["id"] is not None else None,
                    name=row["name"],
                    dsl=row["dsl"] or {},
                    enabled=bool(row["enabled"]),
                )
                for row in rows
            ]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("rules.load_failed", extra={"error": str(exc)})
            return []

    return []


async def evaluate(
    event_kind: str,
    event_payload: dict[str, Any],
    db: Any,
) -> list[Alert]:
    """Evaluate all enabled rules against an event and return matching alerts.

    Failure of an individual rule never aborts the batch; bad rules are
    skipped and logged.
    """
    rules = await _load_rules_from_db(db)
    alerts: list[Alert] = []
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            alert = evaluate_rule(rule, event_kind, event_payload or {})
        except Exception as exc:
            logger.warning(
                "rules.eval_failed",
                extra={"rule": rule.name, "error": str(exc)},
            )
            continue
        if alert is not None:
            alerts.append(alert)
    return alerts
