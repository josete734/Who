"""Heuristic 'probable home city' inference from clustered signals.

Strategy:
  1. Aggregate signals at H3 res 6 (city-block scale) via ``aggregator``.
  2. Score each hex by `weight * recency_factor` where the recency
     factor decays by half over 90 days for the most-recent signal in
     the hex.
  3. Pick the top hex; reverse-geocode-via-cache for a label, falling
     back to the country code from any IP/address evidence.

This is a *guess*, never a fact: returned ``confidence`` reflects
ambiguity (gap between the top hex and the runner-up).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass
from typing import Any

from app.geo.aggregator import aggregate


@dataclass(slots=True)
class HomeGuess:
    lat: float
    lon: float
    h3: str
    confidence: float
    label: str | None
    supporting_signals: int
    runner_up_h3: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _recency_factor(latest: dt.datetime | None, *, now: dt.datetime | None = None) -> float:
    if latest is None:
        return 1.0
    now = now or dt.datetime.now(dt.timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)
    age_days = max(0.0, (now - latest).total_seconds() / 86400.0)
    # half-life of 90 days
    return math.pow(0.5, age_days / 90.0)


def _label_from_evidence(rows: list[dict[str, Any]]) -> str | None:
    for r in rows:
        ev = r.get("evidence") or {}
        if isinstance(ev, dict):
            for k in ("city", "place", "query"):
                if isinstance(ev.get(k), str) and ev[k].strip():
                    return ev[k].strip()
    return None


def infer_home(rows: list[dict[str, Any]], *, h3_res: int = 6) -> HomeGuess | None:
    """``rows`` are dicts shaped like ``geo_signals`` (incl. observed_at)."""
    if not rows:
        return None
    # Latest timestamp per cell — a stand-in for "still active here".
    aggs = aggregate(rows, h3_res=h3_res)
    if not aggs:
        return None

    # Build cell -> latest observed_at map for recency weighting.
    latest_by_cell: dict[str, dt.datetime | None] = {}
    rows_by_cell: dict[str, list[dict[str, Any]]] = {}
    from app.geo.aggregator import _latlon_to_cell

    for r in rows:
        try:
            cell = _latlon_to_cell(float(r["lat"]), float(r["lon"]), h3_res)
        except Exception:
            continue
        rows_by_cell.setdefault(cell, []).append(r)
        ts = r.get("observed_at")
        if isinstance(ts, str):
            try:
                ts = dt.datetime.fromisoformat(ts)
            except ValueError:
                ts = None
        if ts is not None:
            cur = latest_by_cell.get(cell)
            if cur is None or ts > cur:
                latest_by_cell[cell] = ts

    scored = []
    for h in aggs:
        score = h.weight * _recency_factor(latest_by_cell.get(h.h3))
        scored.append((score, h))
    scored.sort(key=lambda x: x[0], reverse=True)

    top_score, top = scored[0]
    runner = scored[1][1].h3 if len(scored) > 1 else None
    runner_score = scored[1][0] if len(scored) > 1 else 0.0

    # Confidence = relative dominance of the top hex.
    if top_score <= 0:
        return None
    gap = (top_score - runner_score) / top_score if top_score > 0 else 0.0
    confidence = round(min(0.95, 0.3 + 0.6 * gap), 3)

    label = _label_from_evidence(rows_by_cell.get(top.h3, []))

    return HomeGuess(
        lat=top.lat,
        lon=top.lon,
        h3=top.h3,
        confidence=confidence,
        label=label,
        supporting_signals=top.count,
        runner_up_h3=runner,
    )


async def infer_home_for_case(case_id: str, *, h3_res: int = 6) -> HomeGuess | None:
    from sqlalchemy import text
    from app.db import session_scope

    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT lat, lon, accuracy_km, kind, source_collector,
                           confidence, evidence, NULL AS observed_at
                      FROM geo_signals
                     WHERE case_id = :cid
                    """
                ),
                {"cid": str(case_id)},
            )
        ).mappings().all()
    return infer_home([dict(r) for r in rows], h3_res=h3_res)
