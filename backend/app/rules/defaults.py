"""Built-in rule definitions seeded at deploy time.

Each entry is a ``(name, dsl, enabled)`` tuple. The migration / a startup
hook is expected to upsert these into the ``rules`` table by name.

Rules covered:
1. ``leaked_password``         — HIBP password collector hit
2. ``darkweb_hit``             — Ahmia onion search hit
3. ``ct_new_cert``             — Newly issued TLS cert via CT watcher
4. ``breach_email``            — Email observed in a breach combo list
5. ``contradictory_identities``— Same entity carries 2+ distinct real_name
                                 attribute values
6. ``high_confidence_entity``  — Entity resolution score above 0.9
"""
from __future__ import annotations

from typing import Any

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "name": "leaked_password",
        "enabled": True,
        "dsl": {
            "when": "collector.result",
            "if": {
                "collector": {"eq": "hibp_passwords"},
                "payload.hits": {"gt": 0},
            },
            "then": {
                "alert": "high",
                "message": "Leaked password detected for {{ payload.email or payload.identifier or 'subject' }} ({{ payload.hits }} hit(s)).",
            },
        },
    },
    {
        "name": "darkweb_hit",
        "enabled": True,
        "dsl": {
            "when": "collector.result",
            "if": {
                "collector": {"eq": "ahmia"},
                "payload.hits": {"gt": 0},
            },
            "then": {
                "alert": "high",
                "message": "Darkweb mention found via ahmia for query '{{ payload.query }}' ({{ payload.hits }} result(s)).",
            },
        },
    },
    {
        "name": "ct_new_cert",
        "enabled": True,
        "dsl": {
            "when": "ct.new_cert",
            "if": {
                "payload.domain": {"regex": ".+"},
            },
            "then": {
                "alert": "medium",
                "message": "New TLS certificate observed for {{ payload.domain }} (issuer: {{ payload.issuer or 'unknown' }}).",
            },
        },
    },
    {
        "name": "breach_email",
        "enabled": True,
        "dsl": {
            "when": "collector.result",
            "if": {
                "collector": {"in": ["dehashed", "hibp", "breach_index"]},
                "payload.email": {"regex": ".+@.+"},
            },
            "then": {
                "alert": "high",
                "message": "Email {{ payload.email }} found in breach source '{{ payload.collector }}'.",
            },
        },
    },
    {
        "name": "contradictory_identities",
        "enabled": True,
        "dsl": {
            "when": "entity.updated",
            "if": {
                "payload.real_name_count": {"gt": 1},
            },
            "then": {
                "alert": "high",
                "message": "Entity {{ payload.entity_id }} has {{ payload.real_name_count }} contradictory real_name values: {{ payload.real_names }}.",
            },
        },
    },
    {
        "name": "high_confidence_entity",
        "enabled": True,
        "dsl": {
            "when": "entity.resolved",
            "if": {
                "payload.score": {"gt": 0.9},
            },
            "then": {
                "alert": "medium",
                "message": "High-confidence entity match (score={{ payload.score }}) for entity {{ payload.entity_id }}.",
            },
        },
    },
]


def iter_default_rules():
    """Yield ``(name, dsl, enabled)`` triples for seeding."""
    for r in DEFAULT_RULES:
        yield r["name"], r["dsl"], r["enabled"]
