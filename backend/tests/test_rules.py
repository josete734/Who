"""Tests for the rules DSL evaluator and Jinja templating (Wave 4 / D5)."""
from __future__ import annotations

import pytest

from app.rules.defaults import DEFAULT_RULES, iter_default_rules
from app.rules.dsl import Alert, Rule, evaluate_rule, render_message
from app.rules.engine import evaluate


def _rule(name: str, dsl: dict) -> Rule:
    return Rule(id=None, name=name, dsl=dsl, enabled=True)


# ---------------------------------------------------------------------------
# Operator coverage
# ---------------------------------------------------------------------------
def test_eq_op_matches_and_misses():
    rule = _rule(
        "eq",
        {
            "when": "collector.result",
            "if": {"collector": {"eq": "hibp_passwords"}},
            "then": {"alert": "high", "message": "hit"},
        },
    )
    assert evaluate_rule(rule, "collector.result", {"collector": "hibp_passwords"}) is not None
    assert evaluate_rule(rule, "collector.result", {"collector": "ahmia"}) is None
    # `when` mismatch shortcuts.
    assert evaluate_rule(rule, "other.kind", {"collector": "hibp_passwords"}) is None


def test_in_contains_regex_gt():
    rule = _rule(
        "ops",
        {
            "when": "e",
            "if": {
                "collector": {"in": ["a", "b"]},
                "payload.text": {"contains": "leak"},
                "payload.email": {"regex": ".+@.+"},
                "payload.score": {"gt": 0.5},
            },
            "then": {"alert": "high", "message": "ok"},
        },
    )
    payload = {"collector": "a", "text": "found leak today", "email": "x@y", "score": 0.9}
    assert evaluate_rule(rule, "e", payload) is not None
    # Each operator can independently fail.
    assert evaluate_rule(rule, "e", {**payload, "collector": "z"}) is None
    assert evaluate_rule(rule, "e", {**payload, "text": "clean"}) is None
    assert evaluate_rule(rule, "e", {**payload, "email": "no-at"}) is None
    assert evaluate_rule(rule, "e", {**payload, "score": 0.1}) is None


def test_missing_field_does_not_fire():
    rule = _rule(
        "miss",
        {"when": "k", "if": {"payload.absent": {"eq": 1}}, "then": {"alert": "low", "message": "x"}},
    )
    assert evaluate_rule(rule, "k", {}) is None


def test_unknown_operator_raises():
    rule = _rule(
        "bad",
        {"when": "k", "if": {"collector": {"lt": 1}}, "then": {"alert": "low", "message": "x"}},
    )
    with pytest.raises(ValueError):
        evaluate_rule(rule, "k", {"collector": 1})


# ---------------------------------------------------------------------------
# Jinja templating
# ---------------------------------------------------------------------------
def test_jinja_message_renders_payload_fields():
    msg = render_message(
        "Leak for {{ payload.email }} ({{ payload.hits }})",
        {"kind": "x", "payload": {"email": "a@b.com", "hits": 3}},
    )
    assert msg == "Leak for a@b.com (3)"


def test_jinja_falls_back_on_error():
    # StrictUndefined would normally raise; the fallback returns the raw template.
    out = render_message("Hello {{ missing }}", {"kind": "x", "payload": {}})
    assert out == "Hello {{ missing }}"


def test_alert_carries_rendered_message_and_level():
    rule = _rule(
        "leaked_password",
        {
            "when": "collector.result",
            "if": {"collector": {"eq": "hibp_passwords"}, "payload.hits": {"gt": 0}},
            "then": {"alert": "high", "message": "Leak for {{ payload.email }} x{{ payload.hits }}"},
        },
    )
    alert = evaluate_rule(
        rule,
        "collector.result",
        {"collector": "hibp_passwords", "email": "u@x", "hits": 2, "case_id": "c1"},
    )
    assert isinstance(alert, Alert)
    assert alert.level == "high"
    assert alert.message == "Leak for u@x x2"
    assert alert.case_id == "c1"
    assert alert.rule_name == "leaked_password"


# ---------------------------------------------------------------------------
# Engine async path with stub DB
# ---------------------------------------------------------------------------
class _StubDB:
    def __init__(self, rules: list[Rule]):
        self._rules = rules

    async def load_rules(self) -> list[Rule]:
        return self._rules


async def test_engine_evaluates_multiple_rules():
    rules = [
        _rule(
            "leaked_password",
            {
                "when": "collector.result",
                "if": {"collector": {"eq": "hibp_passwords"}, "payload.hits": {"gt": 0}},
                "then": {"alert": "high", "message": "leak {{ payload.email }}"},
            },
        ),
        _rule(
            "darkweb_hit",
            {
                "when": "collector.result",
                "if": {"collector": {"eq": "ahmia"}, "payload.hits": {"gt": 0}},
                "then": {"alert": "high", "message": "dark"},
            },
        ),
    ]
    db = _StubDB(rules)
    alerts = await evaluate(
        "collector.result",
        {"collector": "hibp_passwords", "email": "v@x", "hits": 1},
        db,
    )
    assert len(alerts) == 1
    assert alerts[0].rule_name == "leaked_password"
    assert "leak v@x" in alerts[0].message


async def test_engine_skips_disabled_rules():
    rules = [
        Rule(
            id=None,
            name="off",
            enabled=False,
            dsl={"when": "k", "if": {}, "then": {"alert": "high", "message": "x"}},
        ),
    ]
    alerts = await evaluate("k", {}, _StubDB(rules))
    assert alerts == []


async def test_engine_accepts_plain_list_of_rules():
    rules = [
        _rule(
            "any_kind",
            {"if": {"payload.x": {"eq": 1}}, "then": {"alert": "low", "message": "ok"}},
        ),
    ]
    alerts = await evaluate("anything", {"x": 1}, rules)
    assert len(alerts) == 1
    assert alerts[0].level == "low"


# ---------------------------------------------------------------------------
# Defaults sanity
# ---------------------------------------------------------------------------
def test_defaults_cover_required_six():
    names = {r["name"] for r in DEFAULT_RULES}
    assert names == {
        "leaked_password",
        "darkweb_hit",
        "ct_new_cert",
        "breach_email",
        "contradictory_identities",
        "high_confidence_entity",
    }
    # iter_default_rules yields tuples.
    triples = list(iter_default_rules())
    assert len(triples) == 6
    for name, dsl, enabled in triples:
        assert isinstance(name, str)
        assert isinstance(dsl, dict)
        assert "then" in dsl
        assert enabled is True


def test_default_high_confidence_entity_fires_above_threshold():
    rule_dsl = next(r["dsl"] for r in DEFAULT_RULES if r["name"] == "high_confidence_entity")
    rule = _rule("high_confidence_entity", rule_dsl)
    assert evaluate_rule(rule, "entity.resolved", {"score": 0.95, "entity_id": "e1"}) is not None
    assert evaluate_rule(rule, "entity.resolved", {"score": 0.5, "entity_id": "e1"}) is None


def test_default_contradictory_identities_requires_multiple_real_names():
    rule_dsl = next(r["dsl"] for r in DEFAULT_RULES if r["name"] == "contradictory_identities")
    rule = _rule("contradictory_identities", rule_dsl)
    fired = evaluate_rule(
        rule,
        "entity.updated",
        {"entity_id": "e1", "real_name_count": 2, "real_names": ["A", "B"]},
    )
    assert fired is not None and fired.level == "high"
    assert evaluate_rule(rule, "entity.updated", {"real_name_count": 1}) is None
