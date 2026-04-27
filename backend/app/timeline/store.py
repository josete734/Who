"""Persistence layer for timeline_events.

We avoid adding a full SQLAlchemy ORM model to keep the timeline
subsystem self-contained: the migration is the source of truth and we
upsert via raw SQL. `case_id` + `kind` + `ts` + `source_collector`
form the natural key for dedup at the DB layer.
"""
from __future__ import annotations

import json
import uuid
from typing import Iterable

from sqlalchemy import text

from app.db import session_scope
from app.timeline.extractor import TimelineEvent


_UPSERT_SQL = text(
    """
    INSERT INTO timeline_events
        (id, case_id, ts, kind, source_collector, label, evidence, confidence, finding_id)
    VALUES
        (:id, :case_id, :ts, :kind, :source_collector, :label,
         CAST(:evidence AS JSONB), :confidence, :finding_id)
    ON CONFLICT DO NOTHING
    """
)

_DELETE_SQL = text("DELETE FROM timeline_events WHERE case_id = :case_id")

_SELECT_SQL = text(
    """
    SELECT id, case_id, ts, kind, source_collector, label, evidence, confidence, finding_id
      FROM timeline_events
     WHERE case_id = :case_id
       AND (:kind IS NULL OR kind = :kind)
       AND (:t_from IS NULL OR ts >= :t_from)
       AND (:t_to   IS NULL OR ts <= :t_to)
     ORDER BY ts ASC
    """
)


async def clear_case(case_id: str | uuid.UUID) -> None:
    async with session_scope() as s:
        await s.execute(_DELETE_SQL, {"case_id": str(case_id)})


async def upsert_events(case_id: str | uuid.UUID, events: Iterable[TimelineEvent]) -> int:
    rows = []
    for ev in events:
        rows.append({
            "id": str(uuid.uuid4()),
            "case_id": str(case_id),
            "ts": ev.ts,
            "kind": ev.kind,
            "source_collector": ev.source_collector,
            "label": ev.label,
            "evidence": json.dumps(ev.evidence or {}, default=str),
            "confidence": float(ev.confidence),
            "finding_id": ev.finding_id,
        })
    if not rows:
        return 0
    async with session_scope() as s:
        for row in rows:
            await s.execute(_UPSERT_SQL, row)
    return len(rows)


async def fetch_events(
    case_id: str | uuid.UUID,
    *,
    kind: str | None = None,
    t_from=None,
    t_to=None,
) -> list[dict]:
    async with session_scope() as s:
        result = await s.execute(
            _SELECT_SQL,
            {"case_id": str(case_id), "kind": kind, "t_from": t_from, "t_to": t_to},
        )
        return [dict(r._mapping) for r in result]
