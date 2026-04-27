"""Arq task: post-processing triangulation of activity polylines.

Reads findings emitted by collectors that produce GPS polylines (e.g.
Strava), feeds them to ``app.spatial.triangulation.infer_locations``,
persists each ``InferredLocation`` into the ``inferred_locations`` table,
emits a ``location`` finding, and publishes an ``inferred_location``
event over the Redis bus for SSE consumers.

Designed to be fail-soft so a triangulation failure never breaks a case.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db import Finding as FindingRow, session_scope
from app.event_bus import publish
from app.spatial.triangulation import Activity, InferredLocation, infer_locations

log = logging.getLogger(__name__)


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    # Tolerate trailing Z and naive ISO-8601 strings.
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


async def _load_polyline_findings(case_id: uuid.UUID) -> list[Activity]:
    """Read all findings for the case whose payload has a non-null polyline."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, payload FROM findings "
                    "WHERE case_id = :cid "
                    "AND payload ? 'polyline' "
                    "AND payload->>'polyline' IS NOT NULL "
                    "AND payload->>'polyline' <> ''"
                ),
                {"cid": str(case_id)},
            )
        ).all()

    activities: list[Activity] = []
    for row in rows:
        finding_id, payload = row[0], row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        poly = payload.get("polyline")
        if not poly or not isinstance(poly, str):
            continue
        activities.append(
            Activity(
                id=str(finding_id),
                polyline=poly,
                start_dt=_parse_dt(payload.get("start_date_local")),
                kind_hint=payload.get("sport_type"),
            )
        )
    return activities


async def _persist_inferred(case_id: uuid.UUID, loc: InferredLocation) -> None:
    """Insert the inferred_locations row + a location Finding for the case."""
    finding_id = uuid.uuid4()
    fingerprint = f"inferred:{loc.kind}:{round(loc.lat, 4)}:{round(loc.lon, 4)}"
    title = f"{loc.kind} ({loc.lat:.4f}, {loc.lon:.4f})"[:500]
    payload = {
        "kind": loc.kind,
        "lat": loc.lat,
        "lon": loc.lon,
        "radius_m": loc.radius_m,
        "confidence": loc.confidence,
        "evidence": loc.evidence,
        "source_activity_count": len(loc.source_finding_ids),
        "source_finding_ids": loc.source_finding_ids,
    }

    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO inferred_locations "
                "(case_id, kind, lat, lon, radius_m, confidence, evidence, source_finding_ids) "
                "VALUES (:cid, :kind, :lat, :lon, :r, :conf, "
                "CAST(:ev AS JSONB), CAST(:src AS JSONB))"
            ),
            {
                "cid": str(case_id),
                "kind": loc.kind,
                "lat": loc.lat,
                "lon": loc.lon,
                "r": int(loc.radius_m),
                "conf": float(loc.confidence),
                "ev": json.dumps(loc.evidence),
                "src": json.dumps(loc.source_finding_ids),
            },
        )
        s.add(
            FindingRow(
                id=finding_id,
                case_id=case_id,
                collector="triangulation",
                category="location",
                entity_type="location",
                title=title,
                url=None,
                confidence=float(loc.confidence),
                payload=payload,
                fingerprint=fingerprint,
            )
        )


async def run_triangulation(ctx: dict[str, Any], case_id_str: str) -> int:
    """Arq entrypoint. Returns the number of inferred locations emitted."""
    try:
        case_id = uuid.UUID(case_id_str)
    except (TypeError, ValueError):
        log.warning("triangulation.bad_case_id case_id=%s", case_id_str)
        return 0

    try:
        activities = await _load_polyline_findings(case_id)
    except Exception as e:  # noqa: BLE001
        log.warning("triangulation.load_failed case=%s err=%s", case_id, e)
        return 0

    if len(activities) < 5:
        return 0

    try:
        locations = infer_locations(activities)
    except Exception as e:  # noqa: BLE001
        log.warning("triangulation.infer_failed case=%s err=%s", case_id, e)
        return 0

    emitted = 0
    for loc in locations:
        try:
            await _persist_inferred(case_id, loc)
        except Exception as e:  # noqa: BLE001
            log.warning("triangulation.persist_failed case=%s err=%s", case_id, e)
            continue
        try:
            await publish(
                case_id,
                {
                    "type": "inferred_location",
                    "case_id": str(case_id),
                    "data": {
                        "kind": loc.kind,
                        "lat": loc.lat,
                        "lon": loc.lon,
                        "radius_m": loc.radius_m,
                    },
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning("triangulation.publish_failed case=%s err=%s", case_id, e)
        emitted += 1
    return emitted
