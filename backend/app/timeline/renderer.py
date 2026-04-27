"""Bucket timeline events for UI consumption."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, Iterable, Literal

from app.timeline.extractor import TimelineEvent

Granularity = Literal["day", "month", "year"]


def _bucket_key(ts: dt.datetime, granularity: Granularity) -> str:
    if granularity == "day":
        return ts.strftime("%Y-%m-%d")
    if granularity == "year":
        return ts.strftime("%Y")
    return ts.strftime("%Y-%m")


def _event_to_dict(ev: TimelineEvent) -> dict[str, Any]:
    return {
        "ts": ev.ts.isoformat(),
        "kind": ev.kind,
        "source_collector": ev.source_collector,
        "label": ev.label,
        "evidence": ev.evidence,
        "confidence": ev.confidence,
        "finding_id": ev.finding_id,
    }


def render_timeline(
    events: Iterable[TimelineEvent | dict[str, Any]],
    *,
    granularity: Granularity = "month",
) -> dict[str, Any]:
    """Group `events` into buckets and produce a JSON-serialisable payload.

    Output shape:
        {
          "granularity": "month",
          "buckets": [{"key": "2023-01", "count": 3, "event_ids": [...]}],
          "events": [ ... ordered by ts ascending ... ],
          "kinds":   {"breach": 2, "post": 1},
          "range":   {"from": "...", "to": "..."}
        }
    """
    norm: list[dict[str, Any]] = []
    for ev in events:
        d = ev if isinstance(ev, dict) else _event_to_dict(ev)
        norm.append(d)

    norm.sort(key=lambda d: d["ts"])

    buckets: dict[str, list[int]] = defaultdict(list)
    kinds: dict[str, int] = defaultdict(int)
    for idx, ev in enumerate(norm):
        ts = ev["ts"]
        if isinstance(ts, str):
            ts_dt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            ts_dt = ts
        key = _bucket_key(ts_dt, granularity)
        buckets[key].append(idx)
        kinds[ev["kind"]] += 1

    bucket_list = [
        {"key": k, "count": len(idxs), "event_ids": idxs}
        for k, idxs in sorted(buckets.items())
    ]

    rng: dict[str, str | None] = {"from": None, "to": None}
    if norm:
        rng["from"] = norm[0]["ts"] if isinstance(norm[0]["ts"], str) else norm[0]["ts"].isoformat()
        rng["to"] = norm[-1]["ts"] if isinstance(norm[-1]["ts"], str) else norm[-1]["ts"].isoformat()

    # Ensure ts is a string in the output for JSON-friendliness.
    for ev in norm:
        if not isinstance(ev["ts"], str):
            ev["ts"] = ev["ts"].isoformat()

    return {
        "granularity": granularity,
        "buckets": bucket_list,
        "events": norm,
        "kinds": dict(kinds),
        "range": rng,
    }
