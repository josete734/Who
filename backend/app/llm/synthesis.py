"""Aggregate all findings of a case and ask the LLM for a Markdown profile."""
from __future__ import annotations

import json
import uuid
from collections import Counter

import orjson
from sqlalchemy import select, update

from app.db import Case, Finding, session_scope
from app.event_bus import publish
from app.llm.claude import claude_generate
from app.llm.gemini import gemini_generate
from app.llm.ollama import ollama_generate
from app.llm.openai_client import openai_generate
from app.llm.prompts import USER_TEMPLATE


def _input_block(input_payload: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in input_payload.items()) or "(sin input)"


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
                select(Finding).where(Finding.case_id == case_id).order_by(Finding.collector, Finding.created_at)
            )
        ).scalars().all()

    if not findings:
        await publish(case_id, {
            "type": "synthesis",
            "case_id": str(case_id),
            "data": {"status": "no_findings"},
        })
        return

    # Shrink to a JSON that fits comfortably
    compact: list[dict] = []
    for f in findings:
        compact.append({
            "collector": f.collector,
            "category": f.category,
            "entity": f.entity_type,
            "title": f.title,
            "url": f.url,
            "conf": round(float(f.confidence), 2),
            "payload": f.payload,
        })
    # Cap to ~400KB to keep prompts manageable; truncate deterministically
    serialized = orjson.dumps(compact, option=orjson.OPT_INDENT_2).decode()
    if len(serialized) > 400_000:
        serialized = orjson.dumps(compact[:1500], option=orjson.OPT_INDENT_2).decode()

    prompt = USER_TEMPLATE.format(
        input_block=_input_block(case.input_payload or {}),
        findings_json=serialized,
    )

    if llm == "gemini":
        text, model = await gemini_generate(prompt)
    elif llm == "ollama":
        text, model = await ollama_generate(prompt)
    elif llm == "openai":
        text, model = await openai_generate(prompt)
    elif llm == "claude":
        text, model = await claude_generate(prompt)
    else:
        raise ValueError(f"Unknown LLM: {llm}")

    counts = Counter(f.collector for f in findings)
    summary = {
        "collectors": dict(counts),
        "total_findings": len(findings),
    }

    async with session_scope() as sess:
        await sess.execute(
            update(Case)
            .where(Case.id == case_id)
            .values(
                synthesis_markdown=text,
                synthesis_model=model,
                synthesis_json=summary,
            )
        )

    await publish(case_id, {
        "type": "synthesis",
        "case_id": str(case_id),
        "data": {"status": "done", "model": model, "summary": summary},
    })
