"""Build a per-case timeline from all stored findings."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Iterable

from sqlalchemy import select

from app.db import Finding, session_scope
from app.timeline.extractor import TimelineEvent, extract_events
from app.timeline.store import clear_case, upsert_events

# Two events of the same kind within this window are considered the same.
_DEDUP_TOLERANCE = dt.timedelta(hours=1)


def dedupe_events(events: Iterable[TimelineEvent]) -> list[TimelineEvent]:
    """Collapse near-duplicate events.

    Two events match when they share `kind` *and* their timestamps fall
    within `_DEDUP_TOLERANCE`. The higher-confidence event wins; ties
    keep the earlier one. Evidence from the loser is appended under
    `evidence.merged`.
    """
    items = sorted(events, key=lambda e: (e.kind, e.ts))
    out: list[TimelineEvent] = []
    for ev in items:
        merged = False
        for kept in reversed(out):
            if kept.kind != ev.kind:
                # sorted by kind, so once we see a different kind we can stop.
                break
            if abs(kept.ts - ev.ts) <= _DEDUP_TOLERANCE:
                if ev.confidence > kept.confidence:
                    # Promote the new one but remember the displaced.
                    displaced = kept
                    out.remove(kept)
                    ev_merged = TimelineEvent(
                        ts=ev.ts,
                        kind=ev.kind,
                        source_collector=ev.source_collector,
                        label=ev.label,
                        evidence={**ev.evidence, "merged": _merge_payload(displaced)},
                        confidence=ev.confidence,
                        finding_id=ev.finding_id,
                    )
                    out.append(ev_merged)
                else:
                    kept.evidence.setdefault("merged", []).append(_merge_payload(ev))
                merged = True
                break
        if not merged:
            out.append(ev)
    return sorted(out, key=lambda e: e.ts)


def _merge_payload(ev: TimelineEvent) -> dict:
    return {
        "ts": ev.ts.isoformat(),
        "source_collector": ev.source_collector,
        "confidence": ev.confidence,
        "finding_id": ev.finding_id,
    }


async def _load_findings(case_id: str | uuid.UUID) -> list[Finding]:
    async with session_scope() as s:
        result = await s.execute(select(Finding).where(Finding.case_id == case_id))
        return list(result.scalars())


async def build_timeline(case_id: str | uuid.UUID, *, persist: bool = True) -> list[TimelineEvent]:
    """Read all findings for a case, extract + dedupe, optionally persist."""
    findings = await _load_findings(case_id)
    raw: list[TimelineEvent] = []
    for f in findings:
        raw.extend(extract_events(f))
    deduped = dedupe_events(raw)
    if persist:
        await clear_case(case_id)
        await upsert_events(case_id, deduped)
    return deduped
