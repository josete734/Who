"""Aggregate all findings of a case and ask the LLM for a structured profile.

The synthesis pipeline:

1. Loads the case and all its findings ordered by ``confidence DESC``.
2. Builds a compact view (TOP-50) plus a small ``aggregates`` block with
   inferred locations, entities and photo-cluster summaries so the LLM can
   reason without being flooded by raw payloads.
3. Calls the configured LLM and tries to parse a strict JSON profile out of
   the response. Falls back to a Markdown dossier if JSON parsing fails so
   the legacy ``synthesis_markdown`` field is never empty.
4. Persists the parsed profile in a ``profiles`` table (auto-created if the
   migration has not been applied yet) keyed by ``case_id``.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from typing import Any

import orjson
from sqlalchemy import select, text, update

from app.db import Case, Finding, session_scope
from app.event_bus import publish
from app.llm.claude import claude_generate
from app.llm.gemini import gemini_generate
from app.llm.ollama import ollama_generate
from app.llm.openai_client import openai_generate
from app.llm.prompts import (
    SYSTEM_PROMPT_JSON,
    USER_TEMPLATE,
    USER_TEMPLATE_JSON,
)

log = logging.getLogger(__name__)

# Hard ceiling on the size of the serialized findings block sent to the LLM.
_MAX_FINDINGS_BYTES = 180_000
_TOP_N = 50


def _input_block(input_payload: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in input_payload.items()) or "(sin input)"


def _finding_view(f: Finding) -> dict[str, Any]:
    """Lightweight projection of a Finding suitable for LLM context."""
    payload = f.payload or {}
    # Trim very chatty payload keys to avoid blowing the context window.
    if isinstance(payload, dict):
        payload = {
            k: payload[k]
            for k in list(payload.keys())[:12]
        }
    return {
        "id": str(f.id),
        "collector": f.collector,
        "category": f.category,
        "entity": f.entity_type,
        "title": (f.title or "")[:240],
        "url": f.url,
        "conf": round(float(f.confidence), 2),
        "payload": payload,
    }


async def _load_aggregates(case_id: uuid.UUID) -> dict[str, Any]:
    """Best-effort summary of derived tables (inferred_locations, entities,
    photo_clusters). Each query is wrapped so a missing table doesn't break
    synthesis on older schemas.
    """
    out: dict[str, Any] = {
        "inferred_locations": [],
        "entities": {"people": [], "accounts": [], "domains": []},
        "photo_clusters": [],
    }
    async with session_scope() as sess:
        try:
            rows = (
                await sess.execute(
                    text(
                        "SELECT city, region, country, address, confidence, "
                        "rationale FROM inferred_locations WHERE case_id = :cid "
                        "ORDER BY confidence DESC NULLS LAST LIMIT 10"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().all()
            out["inferred_locations"] = [dict(r) for r in rows]
        except Exception:
            log.debug("inferred_locations not available", exc_info=True)
        try:
            rows = (
                await sess.execute(
                    text(
                        "SELECT kind, value, confidence FROM entities "
                        "WHERE case_id = :cid ORDER BY confidence DESC NULLS LAST "
                        "LIMIT 60"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().all()
            buckets: dict[str, list[dict[str, Any]]] = {
                "people": [],
                "accounts": [],
                "domains": [],
            }
            for r in rows:
                kind = (r.get("kind") or "").lower()
                bucket = (
                    "people" if kind in ("person", "name")
                    else "accounts" if kind in ("username", "email", "phone")
                    else "domains" if kind in ("domain", "url")
                    else None
                )
                if bucket:
                    buckets[bucket].append(dict(r))
            out["entities"] = buckets
        except Exception:
            log.debug("entities table not available", exc_info=True)
        try:
            rows = (
                await sess.execute(
                    text(
                        "SELECT cluster_id, COUNT(*) AS n FROM photos "
                        "WHERE case_id = :cid AND cluster_id IS NOT NULL "
                        "GROUP BY cluster_id ORDER BY n DESC LIMIT 10"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().all()
            out["photo_clusters"] = [dict(r) for r in rows]
        except Exception:
            log.debug("photos table not available", exc_info=True)
    return out


def _extract_json(text_in: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of an LLM response.

    Tolerates ```json fences``` and leading/trailing prose. Returns None if
    nothing parseable is found.
    """
    if not text_in:
        return None
    # Strip code fences.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_in, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # First top-level brace block.
        start = text_in.find("{")
        end = text_in.rfind("}")
        if start != -1 and end > start:
            candidate = text_in[start : end + 1]
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        try:
            return orjson.loads(candidate)
        except Exception:
            return None


_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL UNIQUE,
    body JSONB NOT NULL DEFAULT '{}'::jsonb,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _persist_profile(
    case_id: uuid.UUID,
    body: dict[str, Any],
    model: str,
    *,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    """UPSERT into ``profiles`` (table is auto-created if missing)."""
    async with session_scope() as sess:
        try:
            await sess.execute(text(_PROFILES_DDL))
        except Exception:
            log.warning("profiles DDL failed", exc_info=True)
        try:
            await sess.execute(
                text(
                    "INSERT INTO profiles (case_id, body, model, tokens_in, tokens_out) "
                    "VALUES (:cid, CAST(:body AS jsonb), :model, :tin, :tout) "
                    "ON CONFLICT (case_id) DO UPDATE SET "
                    "body = EXCLUDED.body, model = EXCLUDED.model, "
                    "tokens_in = EXCLUDED.tokens_in, tokens_out = EXCLUDED.tokens_out, "
                    "created_at = now()"
                ),
                {
                    "cid": str(case_id),
                    "body": json.dumps(body, default=str),
                    "model": model,
                    "tin": tokens_in,
                    "tout": tokens_out,
                },
            )
        except Exception:
            log.exception("failed to persist profile")


async def _llm_call(llm: str, prompt: str) -> tuple[str, str]:
    if llm == "gemini":
        return await gemini_generate(prompt)
    if llm == "ollama":
        return await ollama_generate(prompt)
    if llm == "openai":
        return await openai_generate(prompt)
    if llm == "claude":
        return await claude_generate(prompt)
    raise ValueError(f"Unknown LLM: {llm}")


async def synthesize(case_id: uuid.UUID, llm: str) -> None:
    await publish(case_id, {
        "type": "synthesis",
        "case_id": str(case_id),
        "data": {"status": "starting", "llm": llm},
    })

    async with session_scope() as sess:
        case = (await sess.execute(select(Case).where(Case.id == case_id))).scalar_one()
        findings = (
            await sess.execute(
                select(Finding)
                .where(Finding.case_id == case_id)
                .order_by(Finding.confidence.desc(), Finding.created_at)
            )
        ).scalars().all()

    if not findings:
        await publish(case_id, {
            "type": "synthesis",
            "case_id": str(case_id),
            "data": {"status": "no_findings"},
        })
        return

    # TOP-N by confidence; trim further if context is still too big.
    top = findings[:_TOP_N]
    compact = [_finding_view(f) for f in top]
    serialized = orjson.dumps(compact, option=orjson.OPT_INDENT_2).decode()
    while len(serialized) > _MAX_FINDINGS_BYTES and len(compact) > 5:
        compact = compact[: max(5, len(compact) - 5)]
        serialized = orjson.dumps(compact, option=orjson.OPT_INDENT_2).decode()

    aggregates = await _load_aggregates(case_id)
    aggregates_json = orjson.dumps(aggregates, option=orjson.OPT_INDENT_2).decode()

    prompt = SYSTEM_PROMPT_JSON + "\n\n" + USER_TEMPLATE_JSON.format(
        input_block=_input_block(case.input_payload or {}),
        aggregates_json=aggregates_json,
        findings_json=serialized,
    )

    text_out, model = await _llm_call(llm, prompt)
    profile = _extract_json(text_out)

    counts = Counter(f.collector for f in findings)
    summary = {
        "collectors": dict(counts),
        "total_findings": len(findings),
        "top_used": len(compact),
    }

    if profile is None:
        # Fallback: rerun with the legacy Markdown template so we still ship
        # *something* useful to the user, and persist it as the dossier.
        log.warning("synthesis JSON parse failed for case %s; falling back to markdown", case_id)
        md_prompt = USER_TEMPLATE.format(
            input_block=_input_block(case.input_payload or {}),
            findings_json=serialized,
        )
        md_text, md_model = await _llm_call(llm, md_prompt)
        async with session_scope() as sess:
            await sess.execute(
                update(Case).where(Case.id == case_id).values(
                    synthesis_markdown=md_text,
                    synthesis_model=md_model,
                    synthesis_json=summary,
                )
            )
        await publish(case_id, {
            "type": "synthesis",
            "case_id": str(case_id),
            "data": {"status": "done", "model": md_model, "summary": summary,
                     "format": "markdown"},
        })
        return

    summary["profile"] = {
        "confidence_overall": profile.get("confidence_overall"),
        "has_identity": bool(profile.get("confirmed_identity", {}).get("name")),
        "digital_footprint_count": len(profile.get("digital_footprint") or []),
        "breaches_count": len(profile.get("breaches") or []),
    }

    await _persist_profile(case_id, profile, model)

    async with session_scope() as sess:
        await sess.execute(
            update(Case).where(Case.id == case_id).values(
                synthesis_markdown=text_out,
                synthesis_model=model,
                synthesis_json={**summary, "profile_body": profile},
            )
        )

    await publish(case_id, {
        "type": "synthesis",
        "case_id": str(case_id),
        "data": {"status": "done", "model": model, "summary": summary,
                 "format": "json"},
    })
