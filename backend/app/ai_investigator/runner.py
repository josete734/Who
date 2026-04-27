"""Async loop driving the autonomous investigator.

The runner is provider-agnostic: it consumes any object implementing
`LLMClient.generate_with_tools(...)` which returns a normalised
`LLMTurn` describing either tool-use intentions or a final assistant text.

The actual collector dispatch is delegated to a `CollectorDispatcher`
Protocol so the investigator can be wired to the in-flight Agent A8 layer
without depending on its concrete implementation.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from pydantic import ValidationError
from sqlalchemy import text as sql_text

from app.ai_investigator.prompts import build_system_prompt
from app.ai_investigator.report import InvestigatorReport
from app.ai_investigator.tools import (
    ALL_TOOL_NAMES as _BASE_TOOL_NAMES,
    TOOL_ADD_PIVOT,
    TOOL_DEFINITIONS as _BASE_TOOL_DEFINITIONS,
    TOOL_FINALIZE_REPORT,
    TOOL_GET_ENTITIES,
    TOOL_GET_FINDINGS,
    TOOL_RUN_COLLECTOR,
)

# ---------------------------------------------------------------------------
# Extra tools wired locally (definitions can't live in tools.py per editing
# constraints). They are merged into the base TOOL_DEFINITIONS so providers
# see a single tool list.
# ---------------------------------------------------------------------------

TOOL_DORK_QUERY = "dork_query"
TOOL_GET_INFERRED_LOCATIONS = "get_inferred_locations"
TOOL_REVERSE_GEOCODE = "reverse_geocode"

_EXTRA_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": TOOL_DORK_QUERY,
        "description": (
            "Run a search-engine dork via the local SearXNG instance. Use "
            "for indexed traces (LinkedIn, leaks, contact pages) once you "
            "have an identifier worth pivoting on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "engine": {
                    "type": "string",
                    "description": "SearXNG engine name (duckduckgo, google, bing, ...).",
                },
                "query": {"type": "string", "minLength": 1},
            },
            "required": ["engine", "query"],
        },
    },
    {
        "name": TOOL_GET_INFERRED_LOCATIONS,
        "description": (
            "Return the case's inferred_locations table: aggregated geo "
            "signals derived from EXIF, Strava, social posts, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": TOOL_REVERSE_GEOCODE,
        "description": (
            "Resolve lat/lon to a human-readable postal address using the "
            "public Nominatim service (OpenStreetMap)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
            },
            "required": ["lat", "lon"],
        },
    },
]

TOOL_DEFINITIONS: list[dict[str, Any]] = [*_BASE_TOOL_DEFINITIONS, *_EXTRA_TOOL_DEFINITIONS]
ALL_TOOL_NAMES: set[str] = set(_BASE_TOOL_NAMES) | {
    TOOL_DORK_QUERY,
    TOOL_GET_INFERRED_LOCATIONS,
    TOOL_REVERSE_GEOCODE,
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols & DTOs
# ---------------------------------------------------------------------------


class CollectorDispatcher(Protocol):
    """Thin contract the investigator uses to talk to the dispatch layer.

    Agent A8 is expected to provide a concrete implementation. We accept any
    duck-typed object that fulfils these async methods.
    """

    async def run_collector(
        self, case_id: uuid.UUID, name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def get_findings(
        self, case_id: uuid.UUID, filter: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_entities(self, case_id: uuid.UUID) -> dict[str, Any]: ...

    async def add_pivot(
        self, case_id: uuid.UUID, kind: str, value: str
    ) -> dict[str, Any]: ...


@dataclass
class ToolCall:
    """Normalised tool-call request from any LLM provider."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    """Normalised single-turn LLM output."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    @property
    def stop(self) -> bool:
        return not self.tool_calls


class LLMClient(Protocol):
    """Provider-agnostic tool-use client used by the runner."""

    async def generate_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn: ...


@dataclass
class StepEvent:
    """A single observable event emitted by the runner per loop iteration."""

    step: int
    kind: str  # "tool_call" | "tool_result" | "final" | "error" | "max_steps"
    name: str | None = None
    data: Any = None

    def to_sse(self) -> str:
        """Render as an SSE `data: ...` payload (without the trailing blank line)."""
        body = {
            "step": self.step,
            "kind": self.kind,
            "name": self.name,
            "data": self.data,
        }
        return f"event: {self.kind}\ndata: {json.dumps(body, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Provider wrappers
# ---------------------------------------------------------------------------


async def _claude_tool_client_factory() -> LLMClient:
    """Wrap the Anthropic SDK with a tool-use generate_with_tools method."""
    import anthropic

    from app.config import get_settings

    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    model = s.anthropic_model

    class _ClaudeToolClient:
        async def generate_with_tools(
            self,
            system: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
        ) -> LLMTurn:
            resp = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(block.text)
                elif btype == "tool_use":
                    calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=dict(block.input or {}),
                        )
                    )
            return LLMTurn(text="".join(text_parts), tool_calls=calls, raw=resp)

    return _ClaudeToolClient()


async def _openai_tool_client_factory() -> LLMClient:
    import httpx

    from app.ai_investigator.tools import openai_tools  # noqa: F401  (kept for parity)
    from app.dynamic_settings import get_runtime

    rt = await get_runtime()
    key = rt.get("OPENAI_API_KEY") or ""
    model = rt.get("OPENAI_MODEL") or "gpt-4o-mini"
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    class _OpenAIToolClient:
        async def generate_with_tools(
            self,
            system: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
        ) -> LLMTurn:
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": system}, *messages],
                "tools": tools,
                "temperature": 0.2,
            }
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
            msg = data["choices"][0]["message"]
            calls: list[ToolCall] = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
            return LLMTurn(text=msg.get("content") or "", tool_calls=calls, raw=data)

    return _OpenAIToolClient()


async def _gemini_tool_client_factory() -> LLMClient:
    from google import genai
    from google.genai import types

    from app.dynamic_settings import get_runtime

    rt = await get_runtime()
    key = rt.get("GEMINI_API_KEY") or ""
    model = rt.get("GEMINI_MODEL") or "gemini-2.5-pro"
    if not key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    client = genai.Client(api_key=key)

    class _GeminiToolClient:
        async def generate_with_tools(
            self,
            system: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
        ) -> LLMTurn:
            # Gemini takes tools through GenerateContentConfig.
            contents = [
                {
                    "role": "model" if m.get("role") == "assistant" else m.get("role", "user"),
                    "parts": [{"text": m.get("content", "")}]
                    if isinstance(m.get("content"), str)
                    else m.get("content", []),
                }
                for m in messages
            ]
            resp = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    tools=tools,
                    temperature=0.2,
                ),
            )
            calls: list[ToolCall] = []
            text = ""
            try:
                for cand in resp.candidates or []:
                    for part in cand.content.parts or []:
                        if getattr(part, "function_call", None):
                            fc = part.function_call
                            calls.append(
                                ToolCall(
                                    id=getattr(fc, "id", "") or fc.name,
                                    name=fc.name,
                                    arguments=dict(fc.args or {}),
                                )
                            )
                        elif getattr(part, "text", None):
                            text += part.text
            except Exception:  # pragma: no cover - defensive
                log.exception("gemini tool parse failed")
            return LLMTurn(text=text, tool_calls=calls, raw=resp)

    return _GeminiToolClient()


PROVIDER_FACTORIES: dict[str, Callable[[], Awaitable[LLMClient]]] = {
    "claude": _claude_tool_client_factory,
    "anthropic": _claude_tool_client_factory,
    "openai": _openai_tool_client_factory,
    "gemini": _gemini_tool_client_factory,
}


async def get_llm_client(provider: str) -> LLMClient:
    """Resolve a provider name to an LLMClient, lazily importing SDKs."""
    factory = PROVIDER_FACTORIES.get(provider.lower())
    if not factory:
        raise ValueError(f"unsupported llm provider: {provider!r}")
    return await factory()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class InvestigatorRunner:
    """Drives the LLM ↔ tool loop for one case."""

    def __init__(
        self,
        case_id: uuid.UUID | str,
        dispatcher: CollectorDispatcher,
        llm: LLMClient,
        *,
        max_steps: int = 8,
        language: str = "es",
        case_brief: str | None = None,
    ) -> None:
        self.case_id = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
        self.dispatcher = dispatcher
        self.llm = llm
        self.max_steps = max(1, int(max_steps))
        self.language = language
        self.case_brief = case_brief or f"Investiga el caso {self.case_id}."
        self.system_prompt = build_system_prompt(language)
        self.report: InvestigatorReport | None = None

    @classmethod
    async def from_settings(
        cls,
        case_id: uuid.UUID | str,
        dispatcher: CollectorDispatcher,
        *,
        max_steps: int = 8,
        provider: str | None = None,
        language: str = "es",
        case_brief: str | None = None,
    ) -> "InvestigatorRunner":
        from app.config import get_settings

        provider = provider or get_settings().default_llm
        llm = await get_llm_client(provider)
        return cls(
            case_id=case_id,
            dispatcher=dispatcher,
            llm=llm,
            max_steps=max_steps,
            language=language,
            case_brief=case_brief,
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, call: ToolCall) -> dict[str, Any]:
        if call.name not in ALL_TOOL_NAMES:
            return {"error": f"unknown tool: {call.name}"}

        args = call.arguments or {}
        try:
            if call.name == TOOL_RUN_COLLECTOR:
                return await self.dispatcher.run_collector(
                    self.case_id,
                    str(args.get("name", "")),
                    dict(args.get("inputs") or {}),
                )
            if call.name == TOOL_GET_FINDINGS:
                return {
                    "findings": await self.dispatcher.get_findings(
                        self.case_id, args.get("filter") or {}
                    )
                }
            if call.name == TOOL_GET_ENTITIES:
                return await self.dispatcher.get_entities(self.case_id)
            if call.name == TOOL_ADD_PIVOT:
                return await self.dispatcher.add_pivot(
                    self.case_id,
                    str(args.get("kind", "")),
                    str(args.get("value", "")),
                )
            if call.name == TOOL_DORK_QUERY:
                return await _run_dork_query(
                    str(args.get("engine") or "duckduckgo"),
                    str(args.get("query") or ""),
                )
            if call.name == TOOL_GET_INFERRED_LOCATIONS:
                return {"locations": await _load_inferred_locations(self.case_id)}
            if call.name == TOOL_REVERSE_GEOCODE:
                return await _reverse_geocode(
                    float(args.get("lat", 0.0)),
                    float(args.get("lon", 0.0)),
                )
            if call.name == TOOL_FINALIZE_REPORT:
                # Defensive: coerce optional fields.
                payload = {
                    "summary": args.get("summary", ""),
                    "confidence_overall": float(args.get("confidence", 0.0)),
                    "gaps": list(args.get("gaps") or []),
                    "key_entities": list(args.get("key_entities") or []),
                    "timeline_highlights": list(args.get("timeline_highlights") or []),
                    "recommendations": list(args.get("recommendations") or []),
                    "breakthrough_moments": list(args.get("breakthrough_moments") or []),
                    "dead_ends": list(args.get("dead_ends") or []),
                    "address_inferred": args.get("address_inferred") or None,
                    "primary_face_match": args.get("primary_face_match") or None,
                }
                try:
                    self.report = InvestigatorReport(**payload)
                except ValidationError as e:
                    return {"error": "invalid report", "detail": e.errors()}
                return {"ok": True, "report": self.report.model_dump()}
        except Exception as exc:  # pragma: no cover - propagated as tool result
            log.exception("tool %s failed", call.name)
            return {"error": str(exc), "tool": call.name}

        return {"error": "unreachable"}

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _format_tool_result(
        self, call: ToolCall, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Serialise a tool result as a user message containing the JSON output.

        Provider-neutral shape: many providers accept a plain user turn echoing
        the tool result as JSON; this keeps the runner trivially testable.
        """
        return {
            "role": "user",
            "content": json.dumps(
                {"tool_use_id": call.id, "name": call.name, "result": result},
                default=str,
            ),
        }

    async def run(self) -> AsyncIterator[StepEvent]:
        """Async-generator: yields step events; final report on success."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self.case_brief}
        ]

        for step in range(1, self.max_steps + 1):
            try:
                turn = await self.llm.generate_with_tools(
                    system=self.system_prompt,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                )
            except Exception as exc:
                yield StepEvent(step=step, kind="error", data={"error": str(exc)})
                return

            # Persist the assistant turn so subsequent calls have context.
            assistant_record: dict[str, Any] = {
                "role": "assistant",
                "content": turn.text or "",
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in turn.tool_calls
                ],
            }
            messages.append(assistant_record)

            if not turn.tool_calls:
                # Model produced text without tools — treat as terminal.
                yield StepEvent(step=step, kind="final", data={"text": turn.text})
                return

            for call in turn.tool_calls:
                yield StepEvent(
                    step=step,
                    kind="tool_call",
                    name=call.name,
                    data={"id": call.id, "arguments": call.arguments},
                )
                result = await self._execute_tool(call)
                yield StepEvent(
                    step=step,
                    kind="tool_result",
                    name=call.name,
                    data=result,
                )
                messages.append(self._format_tool_result(call, result))

                if call.name == TOOL_FINALIZE_REPORT and self.report is not None:
                    yield StepEvent(
                        step=step,
                        kind="final",
                        name=TOOL_FINALIZE_REPORT,
                        data=self.report.model_dump(),
                    )
                    return

        yield StepEvent(
            step=self.max_steps,
            kind="max_steps",
            data={"max_steps": self.max_steps},
        )


# ---------------------------------------------------------------------------
# Local tool implementations (dork_query / get_inferred_locations / reverse_geocode)
# ---------------------------------------------------------------------------


async def _run_dork_query(engine: str, query: str) -> dict[str, Any]:
    """Send a query to the local SearXNG instance and return top results."""
    if not query.strip():
        return {"error": "empty query"}
    base = (
        os.environ.get("SEARXNG_URL")
        or os.environ.get("SEARX_URL")
        or "http://searxng:8080"
    ).rstrip("/")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{base}/search",
                params={
                    "q": query,
                    "format": "json",
                    "engines": engine,
                    "safesearch": 0,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return {"error": f"searxng request failed: {exc}", "engine": engine, "query": query}
    results = []
    for item in (data.get("results") or [])[:15]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
                "engine": item.get("engine"),
            }
        )
    return {"engine": engine, "query": query, "results": results}


async def _load_inferred_locations(case_id: uuid.UUID) -> list[dict[str, Any]]:
    """Read the inferred_locations table for the case (best-effort)."""
    from app.db import session_scope

    try:
        async with session_scope() as sess:
            rows = (
                await sess.execute(
                    sql_text(
                        "SELECT lat, lon, city, region, country, address, "
                        "confidence, source_count, rationale "
                        "FROM inferred_locations WHERE case_id = :cid "
                        "ORDER BY confidence DESC NULLS LAST LIMIT 25"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("inferred_locations unavailable: %s", exc)
        return []


async def _reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    """Resolve lat/lon to a postal address via Nominatim."""
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return {"error": "invalid coordinates", "lat": lat, "lon": lon}
    url = "https://nominatim.openstreetmap.org/reverse"
    headers = {
        "User-Agent": os.environ.get("NOMINATIM_UA", "osint-tool/1.0 (contact: ops@local)"),
        "Accept": "application/json",
    }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                url,
                params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18,
                        "addressdetails": 1},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return {"error": f"nominatim request failed: {exc}", "lat": lat, "lon": lon}
    return {
        "lat": lat,
        "lon": lon,
        "display_name": data.get("display_name"),
        "address": data.get("address") or {},
        "osm_id": data.get("osm_id"),
    }
