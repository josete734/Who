"""LLM-as-parser fallback (Wave 7).

Collectors that scrape HTML rely on CSS selectors / regex that break the
moment the upstream site reshuffles its DOM. This utility lets a collector
opt-in to a graceful fallback:

    from app.collectors._ai_parse import llm_parse_url

    profile = await llm_parse_url(
        "https://www.threads.net/@somebody",
        ProfileSchema,
        hint="Extract Threads public profile fields",
    )

The pipeline is:

1. Convert the URL to clean Markdown via ``app.netfetch.jina.fetch_markdown``.
2. Send the Markdown plus the schema to Gemini Flash (or any other LLM
   the project is configured for).
3. Parse the response into the requested Pydantic model.

The function returns ``None`` on any failure so the caller can drop back
to its legacy path. Confidence emitted by callers using this fallback
should be downgraded (we suggest 0.7) to reflect the parser substitution.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


# Imports kept at module scope so tests can monkeypatch them. The LLM and
# Jina layers are themselves fail-soft.
try:  # pragma: no cover — best-effort
    from app.llm.synthesis import _llm_call as _llm_call_real
    from app.netfetch.jina import fetch_markdown as _fetch_markdown_real
except ImportError:  # pragma: no cover
    _llm_call_real = None  # type: ignore[assignment]
    _fetch_markdown_real = None  # type: ignore[assignment]


__all__ = [
    "AI_PARSE_CONFIDENCE",
    "llm_parse_text",
    "llm_parse_url",
]


# Suggested confidence for findings emitted via the LLM fallback. The
# extraction is best-effort and should not outscore the native parser.
AI_PARSE_CONFIDENCE: float = 0.7


def _build_prompt(schema: Type[BaseModel], text: str, hint: str) -> str:
    """Build a strict JSON-only prompt that asks the LLM to fill the schema."""
    try:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    except Exception:
        schema_json = "{}"
    # Keep the input bounded so we don't blow past Flash's window.
    body = (text or "")[:18000]
    return (
        "Eres un extractor de datos. Lee el siguiente texto y devuelve "
        "EXCLUSIVAMENTE un objeto JSON que cumpla este schema "
        f"(no añadas comentarios, explicaciones ni Markdown):\n\n"
        f"SCHEMA:\n{schema_json}\n\n"
        f"PISTA: {hint}\n\n"
        f"TEXTO:\n{body}\n"
    )


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Pull the first {...} block out of ``raw`` and parse it as JSON."""
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def llm_parse_text(
    text: str,
    schema: Type[T],
    hint: str,
    *,
    llm: str = "gemini",
) -> T | None:
    """Run the LLM on ``text`` and validate the result against ``schema``.

    Returns ``None`` on any failure (no LLM, parse error, schema rejection).
    """
    if not text:
        return None
    if _llm_call_real is None:
        return None
    prompt = _build_prompt(schema, text, hint)
    try:
        raw, _model = await _llm_call_real(llm, prompt)
    except Exception as exc:  # noqa: BLE001
        log.debug("ai_parse.llm_failed llm=%s err=%s", llm, exc)
        return None
    data = _extract_json(raw or "")
    if data is None:
        return None
    try:
        return schema(**data)
    except ValidationError as exc:
        log.debug("ai_parse.schema_failed err=%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("ai_parse.instantiate_failed err=%s", exc)
        return None


async def llm_parse_url(
    url: str,
    schema: Type[T],
    hint: str,
    *,
    llm: str = "gemini",
    redis: Any | None = None,
) -> T | None:
    """Convenience: Jina Reader → ``llm_parse_text`` → Pydantic instance."""
    if _fetch_markdown_real is None:
        return None
    md = await _fetch_markdown_real(url, redis=redis)
    if not md:
        return None
    return await llm_parse_text(md, schema, hint, llm=llm)
