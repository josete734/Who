"""Fan-out orchestrator.

For a given case, runs every applicable Collector concurrently with a
Semaphore, persists Findings + CollectorRun rows, and publishes events to
the Redis event bus for SSE consumers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from typing import Any

from sqlalchemy import select, update

from app.collectors import collector_registry  # noqa: F401 — also triggers import of every collector
from app.collectors.base import Collector, Finding
from app.config import get_settings
from app.db import Case, CollectorRun, Finding as FindingRow, session_scope
from app.event_bus import publish
from app.schemas import SearchInput


async def _run_one(case_id: uuid.UUID, collector: Collector, input: SearchInput) -> None:
    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    seen: set[str] = set()
    count = 0
    run_id = uuid.uuid4()

    async with session_scope() as s:
        s.add(CollectorRun(
            id=run_id,
            case_id=case_id,
            collector=collector.name,
            status="running",
            started_at=started,
        ))

    await publish(case_id, {
        "type": "collector_start",
        "case_id": str(case_id),
        "data": {"collector": collector.name, "category": collector.category},
    })

    status = "ok"
    message: str | None = None

    try:
        async with asyncio.timeout(collector.timeout_seconds):
            async for f in collector.run(input):
                fp = f.fingerprint()
                if fp in seen:
                    continue
                seen.add(fp)
                count += 1
                payload = {
                    "id": str(uuid.uuid4()),
                    "collector": f.collector,
                    "category": f.category,
                    "entity_type": f.entity_type,
                    "title": f.title,
                    "url": f.url,
                    "confidence": f.confidence,
                    "payload": f.payload,
                    "fingerprint": fp,
                }
                async with session_scope() as s:
                    s.add(FindingRow(
                        id=uuid.UUID(payload["id"]),
                        case_id=case_id,
                        collector=f.collector,
                        category=f.category,
                        entity_type=f.entity_type,
                        title=f.title[:500],
                        url=f.url,
                        confidence=f.confidence,
                        payload=f.payload,
                        fingerprint=fp,
                    ))
                await publish(case_id, {
                    "type": "finding",
                    "case_id": str(case_id),
                    "data": payload,
                })
    except asyncio.TimeoutError:
        status = "timeout"
        message = f"Timeout after {collector.timeout_seconds}s"
    except Exception as e:  # noqa: BLE001
        status = "error"
        message = str(e)[:2000]

    duration_ms = int((time.perf_counter() - t0) * 1000)
    async with session_scope() as s:
        await s.execute(
            update(CollectorRun)
            .where(CollectorRun.id == run_id)
            .values(
                status=status,
                findings_count=count,
                duration_ms=duration_ms,
                message=message,
                finished_at=dt.datetime.now(dt.timezone.utc),
            )
        )

    await publish(case_id, {
        "type": "collector_end",
        "case_id": str(case_id),
        "data": {
            "collector": collector.name,
            "status": status,
            "count": count,
            "duration_ms": duration_ms,
            "message": message,
        },
    })


async def run_case(case_id: uuid.UUID, input: SearchInput, llm: str = "claude") -> None:
    """Main entry: fan-out all applicable collectors, then synthesize with LLM."""
    s = get_settings()
    sem = asyncio.Semaphore(s.max_concurrent_collectors)

    # Mark case as running
    async with session_scope() as sess:
        await sess.execute(update(Case).where(Case.id == case_id).values(status="running"))

    collectors = collector_registry.applicable_for(input)
    await publish(case_id, {
        "type": "case_started",
        "case_id": str(case_id),
        "data": {"collectors": [c.name for c in collectors]},
    })

    async def guarded(c: Collector) -> None:
        async with sem:
            await _run_one(case_id, c, input)

    try:
        await asyncio.gather(*(guarded(c) for c in collectors))
    except Exception as e:
        async with session_scope() as sess:
            await sess.execute(
                update(Case)
                .where(Case.id == case_id)
                .values(status="error", error=str(e)[:2000], finished_at=dt.datetime.now(dt.timezone.utc))
            )
        await publish(case_id, {"type": "error", "case_id": str(case_id), "data": {"error": str(e)[:1000]}})
        return

    # Synthesis
    if llm in ("claude", "gemini"):
        try:
            from app.llm.synthesis import synthesize
            await synthesize(case_id, llm)
        except Exception as e:  # noqa: BLE001
            await publish(case_id, {
                "type": "error",
                "case_id": str(case_id),
                "data": {"stage": "synthesis", "error": str(e)[:1000]},
            })

    async with session_scope() as sess:
        await sess.execute(
            update(Case)
            .where(Case.id == case_id)
            .values(status="done", finished_at=dt.datetime.now(dt.timezone.utc))
        )

    await publish(case_id, {"type": "done", "case_id": str(case_id), "data": {}})
