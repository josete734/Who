"""JSON-Schema tool definitions for the autonomous investigator.

These descriptors are provider-agnostic; the runner translates them into the
shape each LLM SDK expects (Anthropic `tools=[...]`, OpenAI function-calling,
Gemini `function_declarations`).
"""
from __future__ import annotations

from typing import Any

# Tool names — kept as constants so callers don't fat-finger string literals.
TOOL_RUN_COLLECTOR = "run_collector"
TOOL_GET_FINDINGS = "get_findings"
TOOL_GET_ENTITIES = "get_entities"
TOOL_ADD_PIVOT = "add_pivot"
TOOL_FINALIZE_REPORT = "finalize_report"

ALL_TOOL_NAMES = {
    TOOL_RUN_COLLECTOR,
    TOOL_GET_FINDINGS,
    TOOL_GET_ENTITIES,
    TOOL_ADD_PIVOT,
    TOOL_FINALIZE_REPORT,
}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": TOOL_RUN_COLLECTOR,
        "description": (
            "Execute an OSINT collector against the current case. Use this to "
            "gather new evidence (e.g. sherlock for usernames, hibp for emails, "
            "crtsh for domains). Only call collectors that are GDPR-compatible "
            "and proportional to the legal basis of the case."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Collector identifier as registered in the registry (e.g. 'sherlock', 'hibp', 'crtsh').",
                },
                "inputs": {
                    "type": "object",
                    "description": "Free-form input dict for the collector (username/email/domain/phone...).",
                    "additionalProperties": True,
                },
            },
            "required": ["name", "inputs"],
        },
    },
    {
        "name": TOOL_GET_FINDINGS,
        "description": (
            "Read findings already stored for the current case. Use a filter to "
            "narrow by collector, category or entity_type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": "Optional filter: {collector?, category?, entity_type?, limit?}",
                    "properties": {
                        "collector": {"type": "string"},
                        "category": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "additionalProperties": False,
                }
            },
            "required": [],
        },
    },
    {
        "name": TOOL_GET_ENTITIES,
        "description": (
            "Return the entity-resolution graph for the case: people, accounts, "
            "domains and their links. Use to reason about the current picture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "Case UUID (string)."},
            },
            "required": ["case_id"],
        },
    },
    {
        "name": TOOL_ADD_PIVOT,
        "description": (
            "Record a new pivot value (username, email, domain, phone) discovered "
            "during the investigation. Pivots feed downstream collectors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["username", "email", "domain", "phone", "name", "url"],
                },
                "value": {"type": "string", "minLength": 1},
            },
            "required": ["kind", "value"],
        },
    },
    {
        "name": TOOL_FINALIZE_REPORT,
        "description": (
            "Stop the investigation and emit the final report. Call this once "
            "you have enough evidence or further collection would be "
            "disproportionate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "gaps": {"type": "array", "items": {"type": "string"}, "default": []},
                "key_entities": {"type": "array", "items": {"type": "string"}, "default": []},
                "timeline_highlights": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "recommendations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["summary", "confidence"],
        },
    },
]


def openai_tools() -> list[dict[str, Any]]:
    """Return TOOL_DEFINITIONS in OpenAI Chat Completions function-calling shape."""
    out: list[dict[str, Any]] = []
    for t in TOOL_DEFINITIONS:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
        )
    return out


def gemini_tools() -> list[dict[str, Any]]:
    """Return TOOL_DEFINITIONS in Gemini `function_declarations` shape."""
    decls: list[dict[str, Any]] = []
    for t in TOOL_DEFINITIONS:
        decls.append(
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }
        )
    return [{"function_declarations": decls}]
