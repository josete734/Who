"""Rules DSL.

A rule is a JSON document with the shape::

    {
      "when": "<event_kind>",                    # required string match
      "if":   {"<dotted.path>": {"<op>": value}, ...},  # AND-combined
      "then": {"alert": "<level>", "message": "<jinja>"}
    }

Supported operators: ``eq``, ``in``, ``contains``, ``regex``, ``gt``.

Field paths use dotted notation (``payload.collector``) and are looked up
against the *event context*: ``{"kind": ..., "payload": ...}``. As a
convenience, top-level keys of ``payload`` are also reachable directly
(e.g. ``collector`` ≡ ``payload.collector``) which keeps rule JSON terse.

Messages are rendered with Jinja2 against the same context, so authors
can write ``"Leaked password for {{ payload.email }}"``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from jinja2 import Environment, StrictUndefined

# Single shared Jinja env. Autoescape is off — alerts are plain text, not HTML.
_JINJA_ENV = Environment(
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


SUPPORTED_OPS = ("eq", "in", "contains", "regex", "gt")


@dataclass
class Rule:
    """In-memory representation of a DB rule row."""

    id: str | None
    name: str
    dsl: dict[str, Any]
    enabled: bool = True


@dataclass
class Alert:
    """Result of a rule firing. Caller persists into ``alerts`` table."""

    rule_id: str | None
    rule_name: str
    level: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    case_id: str | None = None


@dataclass
class RuleMatch:
    """Internal: a successful evaluation, before persistence."""

    rule: Rule
    alert: Alert


# ---------------------------------------------------------------------------
# Field lookup
# ---------------------------------------------------------------------------
_MISSING = object()


def _lookup(ctx: dict[str, Any], path: str) -> Any:
    """Resolve dotted ``path`` against ``ctx``.

    Falls back to ``ctx['payload'][path-head]`` so that authors can write
    bare keys like ``"collector"`` instead of ``"payload.collector"``.
    Returns the sentinel ``_MISSING`` if not found.
    """
    parts = path.split(".")
    cur: Any = ctx
    for i, part in enumerate(parts):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        # First-segment fallback into payload.
        if i == 0 and isinstance(ctx, dict) and isinstance(ctx.get("payload"), dict) and part in ctx["payload"]:
            cur = ctx["payload"][part]
            continue
        return _MISSING
    return cur


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
def _op_eq(left: Any, right: Any) -> bool:
    return left == right


def _op_in(left: Any, right: Any) -> bool:
    if right is None:
        return False
    try:
        return left in right
    except TypeError:
        return False


def _op_contains(left: Any, right: Any) -> bool:
    if left is None:
        return False
    if isinstance(left, str):
        return str(right) in left
    try:
        return right in left
    except TypeError:
        return False


def _op_regex(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        return re.search(right, left) is not None
    except re.error:
        return False


def _op_gt(left: Any, right: Any) -> bool:
    try:
        return left is not None and left > right
    except TypeError:
        return False


_OP_TABLE = {
    "eq": _op_eq,
    "in": _op_in,
    "contains": _op_contains,
    "regex": _op_regex,
    "gt": _op_gt,
}


def _check_predicate(value: Any, predicate: dict[str, Any]) -> bool:
    """Evaluate ``{op: rhs, op2: rhs2, ...}`` against a single field value (AND)."""
    if not isinstance(predicate, dict):
        # Shorthand: bare value means equality.
        return value == predicate
    for op, rhs in predicate.items():
        fn = _OP_TABLE.get(op)
        if fn is None:
            raise ValueError(f"Unsupported operator: {op!r}. Supported: {SUPPORTED_OPS}")
        if not fn(value, rhs):
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_message(template: str, ctx: dict[str, Any]) -> str:
    """Render a Jinja2 message template. Falls back to the raw template on errors."""
    try:
        return _JINJA_ENV.from_string(template).render(**ctx)
    except Exception:
        return template


def evaluate_rule(rule: Rule, event_kind: str, event_payload: dict[str, Any]) -> Alert | None:
    """Evaluate a single ``rule`` against the given event.

    Returns an :class:`Alert` if the rule fires, else ``None``.
    """
    dsl = rule.dsl or {}
    when = dsl.get("when")
    if when is not None and when != event_kind:
        return None

    ctx: dict[str, Any] = {"kind": event_kind, "payload": event_payload or {}}

    conditions = dsl.get("if") or {}
    if not isinstance(conditions, dict):
        return None

    for path, predicate in conditions.items():
        value = _lookup(ctx, path)
        if value is _MISSING:
            return None
        if not _check_predicate(value, predicate):
            return None

    then = dsl.get("then") or {}
    level = str(then.get("alert", "info"))
    template = str(then.get("message", rule.name))
    message = render_message(template, ctx)

    return Alert(
        rule_id=rule.id,
        rule_name=rule.name,
        level=level,
        message=message,
        payload=dict(event_payload or {}),
        case_id=(event_payload or {}).get("case_id"),
    )
