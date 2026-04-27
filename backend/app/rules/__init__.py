"""Wave 4 / D5 — Rules engine.

JSON DSL-driven alerting layer. Rules are stored in the ``rules`` table
(see migration 0005) and evaluated by :func:`app.rules.engine.evaluate`
against incoming events.

The engine is intentionally side-effect free with respect to the bus;
callers are responsible for persisting returned :class:`Alert` records.

Wiring (NOT done here): import the routers in ``app/main.py``::

    from app.routers.rules_router import router as rules_router
    from app.routers.alerts_router import router as alerts_router
    app.include_router(rules_router)
    app.include_router(alerts_router)
"""
from __future__ import annotations

from app.rules.dsl import Alert, Rule, RuleMatch, evaluate_rule, render_message
from app.rules.engine import evaluate

__all__ = [
    "Alert",
    "Rule",
    "RuleMatch",
    "evaluate",
    "evaluate_rule",
    "render_message",
]
