"""Standalone MCP stdio server for the OSINT Tool ("who").

Exposes the FastAPI HTTP API (default https://who.worldmapsound.com) as a
small set of MCP tools so that LLM clients (Claude Desktop, Cursor, etc.)
can drive case creation, execution, retrieval, autonomous investigation,
and export.

This is a thin client: every tool maps to one HTTP call. Auth is via a
bearer token taken from the WHO_API_KEY environment variable; the base URL
comes from WHO_BASE_URL.

Run via the console script ``who-mcp`` (see pyproject.toml) or directly
with ``python -m mcp.server``.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


# NOTE on SDK imports:
# This module lives at `mcp/server.py`; the upstream MCP Python SDK is
# *also* published under the top-level package name `mcp` and ships its
# own `mcp.server` sub-package. To avoid an import-time collision when
# this file is being imported (e.g. during unit tests, before the SDK is
# resolved), we defer SDK imports until `build_server()` is actually
# called. The SDK is only required for the stdio server runtime, not for
# the thin HTTP-wrapping tool functions which the tests exercise.
def _load_sdk():  # pragma: no cover - exercised only with SDK installed
    import importlib

    lowlevel = importlib.import_module("mcp.server.lowlevel")
    stdio = importlib.import_module("mcp.server.stdio")
    types = importlib.import_module("mcp.types")
    return lowlevel.Server, stdio.stdio_server, types.TextContent, types.Tool


# Lightweight stand-in classes used for the static `TOOLS` registry. They
# expose the same `name` / `description` / `inputSchema` attributes that
# the real `mcp.types.Tool` model exposes, so we can build the registry
# at import time without depending on the SDK.
class _ToolStub:
    __slots__ = ("name", "description", "inputSchema")

    def __init__(self, name: str, description: str, inputSchema: dict) -> None:
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


Tool = _ToolStub  # alias used below for the registry literals

DEFAULT_BASE_URL = "https://who.worldmapsound.com"


def _base_url() -> str:
    return os.environ.get("WHO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key = os.environ.get("WHO_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(
            method,
            url,
            headers=_headers(),
            json=json_body,
            params=params,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"HTTP {resp.status_code} from {method} {path}: {resp.text[:500]}"
        )
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        return resp.json()
    return {"status_code": resp.status_code, "body": resp.text}


# ---- Tool implementations ------------------------------------------------


async def osint_create_case(
    inputs: dict,
    legal_basis: str,
    legal_basis_note: str | None = None,
) -> Any:
    """Create a new case (enqueues a run automatically on the backend)."""
    title = (
        inputs.get("title")
        or inputs.get("full_name")
        or inputs.get("email")
        or inputs.get("phone")
        or "MCP case"
    )
    payload = {
        "title": str(title)[:255],
        "legal_basis": legal_basis,
        "input": {k: v for k, v in inputs.items() if k != "title"},
    }
    if legal_basis_note:
        payload["legal_basis_note"] = legal_basis_note
    return await _request("POST", "/api/cases", json_body=payload)


async def osint_run_case(case_id: str) -> Any:
    """Re-trigger a case run (idempotent: returns case status)."""
    return await _request("GET", f"/api/cases/{case_id}")


async def osint_get_findings(
    case_id: str,
    kind: str | None = None,
    collector: str | None = None,
) -> Any:
    """Return findings for a case, optionally filtered by kind/collector."""
    rows = await _request("GET", f"/api/cases/{case_id}/findings")
    if not isinstance(rows, list):
        return rows
    out = rows
    if kind:
        out = [r for r in out if r.get("kind") == kind]
    if collector:
        out = [r for r in out if r.get("collector") == collector]
    return out


async def osint_get_entities(case_id: str, type: str | None = None) -> Any:
    """Return entities extracted for a case, optionally filtered by type."""
    params = {"type": type} if type else None
    return await _request(
        "GET", f"/api/cases/{case_id}/entities", params=params
    )


async def osint_investigate(
    case_id: str,
    provider: str = "gemini",
    max_steps: int = 8,
) -> Any:
    """Kick off the autonomous investigator (SSE stream is buffered)."""
    payload = {"provider": provider, "max_steps": int(max_steps)}
    return await _request(
        "POST", f"/api/cases/{case_id}/investigate", json_body=payload
    )


async def osint_export(case_id: str, format: str) -> Any:
    """Export a case report. format: pdf | stix | misp | json."""
    return await _request(
        "GET",
        f"/api/cases/{case_id}/export",
        params={"format": format},
    )


# ---- MCP tool registry ---------------------------------------------------


TOOLS: list = [
    Tool(
        name="osint_create_case",
        description=(
            "Create a new OSINT case and enqueue its run. "
            "`inputs` is a free-form dict with at least one of "
            "full_name, email, phone, username, domain. "
            "`legal_basis` is a short legal justification string."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "inputs": {"type": "object"},
                "legal_basis": {"type": "string"},
                "legal_basis_note": {"type": ["string", "null"]},
            },
            "required": ["inputs", "legal_basis"],
        },
    ),
    Tool(
        name="osint_run_case",
        description="Fetch / re-poll status of an existing case.",
        inputSchema={
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        },
    ),
    Tool(
        name="osint_get_findings",
        description="List findings for a case, with optional kind/collector filters.",
        inputSchema={
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "kind": {"type": ["string", "null"]},
                "collector": {"type": ["string", "null"]},
            },
            "required": ["case_id"],
        },
    ),
    Tool(
        name="osint_get_entities",
        description="List extracted entities for a case, optionally filtered by type.",
        inputSchema={
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "type": {"type": ["string", "null"]},
            },
            "required": ["case_id"],
        },
    ),
    Tool(
        name="osint_investigate",
        description="Run the autonomous investigator over an existing case.",
        inputSchema={
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "provider": {"type": "string", "default": "gemini"},
                "max_steps": {"type": "integer", "default": 8},
            },
            "required": ["case_id"],
        },
    ),
    Tool(
        name="osint_export",
        description="Export a case report. format: pdf | stix | misp | json.",
        inputSchema={
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "format": {"type": "string"},
            },
            "required": ["case_id", "format"],
        },
    ),
]


_DISPATCH = {
    "osint_create_case": osint_create_case,
    "osint_run_case": osint_run_case,
    "osint_get_findings": osint_get_findings,
    "osint_get_entities": osint_get_entities,
    "osint_investigate": osint_investigate,
    "osint_export": osint_export,
}


def build_server():
    """Construct the MCP Server (lazy-imports the SDK)."""
    SdkServer, _stdio, SdkTextContent, SdkTool = _load_sdk()

    server = SdkServer("who-mcp")

    # Promote our stub Tool entries to real SDK Tool instances.
    sdk_tools = [
        SdkTool(name=t.name, description=t.description, inputSchema=t.inputSchema)
        for t in TOOLS
    ]

    @server.list_tools()
    async def _list_tools():
        return sdk_tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None):
        fn = _DISPATCH.get(name)
        if fn is None:
            raise ValueError(f"unknown tool: {name}")
        args = arguments or {}
        try:
            result = await fn(**args)
        except Exception as exc:  # surface error text to the LLM
            return [SdkTextContent(type="text", text=f"ERROR: {exc}")]
        return [SdkTextContent(type="text", text=json.dumps(result, default=str))]

    return server


async def _amain() -> None:  # pragma: no cover - integration entry
    _, stdio_server, _, _ = _load_sdk()
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    """Console entry point (`who-mcp`)."""
    import asyncio

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
