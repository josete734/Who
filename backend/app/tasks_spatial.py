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
    home_candidates: list[InferredLocation] = []
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
        if loc.kind in ("inferred_home", "inferred_work") and loc.confidence >= 0.7:
            home_candidates.append(loc)

    # Wave 2: second-pass Strava heatmap around each high-confidence inferred
    # home/work centroid. This breaks the prior circular dependency where the
    # heatmap needed a bbox to run, and the bbox was only known after the
    # triangulation. Fail-soft: any error here does not affect the primary
    # triangulation result that the caller already received.
    for loc in home_candidates[:2]:  # cap: at most two re-passes per case
        try:
            await _heatmap_second_pass(case_id, loc.lat, loc.lon)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "triangulation.heatmap_second_pass_failed case=%s err=%s",
                case_id,
                e,
            )

    return emitted


async def _heatmap_second_pass(
    case_id: uuid.UUID, lat: float, lon: float, half_km: float = 1.0
) -> None:
    """Re-run the Strava heatmap collector on a tight bbox around (lat, lon).

    Resolves the prior wave-1 circular dependency: the heatmap needs a bbox
    in extra_context, but the bbox was previously only known once we had
    triangulated polylines — chicken and egg. After triangulation, we now
    inject a 2 km × 2 km bbox around each inferred home/work centroid and
    persist any new hotspot findings produced by the second pass.

    Hotspot findings emitted here carry payload.second_pass=True so the
    consumers can distinguish them from first-pass hotspots.
    """
    try:
        from app.collectors.strava_heatmap import StravaHeatmapCollector
        from app.schemas import SearchInput
    except Exception:  # pragma: no cover
        return

    bbox_str = (
        f"strava_bbox={lat - half_km / 111:.6f},"
        f"{lon - half_km / (111 * 0.7):.6f},"
        f"{lat + half_km / 111:.6f},"
        f"{lon + half_km / (111 * 0.7):.6f}"
    )
    si = SearchInput(extra_context=bbox_str)
    collector = StravaHeatmapCollector()

    persisted = 0
    async with session_scope() as s:
        async for finding in collector.run(si):
            try:
                fid = uuid.uuid4()
                payload = dict(finding.payload or {})
                payload["second_pass"] = True
                payload["centroid_lat"] = lat
                payload["centroid_lon"] = lon
                fp = (
                    f"heatmap2:{round(payload.get('lat', 0.0), 4)}:"
                    f"{round(payload.get('lon', 0.0), 4)}"
                )
                await s.execute(
                    text(
                        "INSERT INTO findings "
                        "(id, case_id, collector, category, entity_type, title, "
                        " url, confidence, payload, fingerprint) "
                        "VALUES (:id, :cid, :col, :cat, :et, :t, :u, :c, "
                        "CAST(:p AS JSONB), :fp) "
                        "ON CONFLICT (case_id, fingerprint) DO NOTHING"
                    ),
                    {
                        "id": fid,
                        "cid": str(case_id),
                        "col": finding.collector,
                        "cat": finding.category,
                        "et": finding.entity_type,
                        "t": finding.title,
                        "u": finding.url,
                        "c": float(finding.confidence),
                        "p": json.dumps(payload),
                        "fp": fp,
                    },
                )
                persisted += 1
            except Exception as e:  # noqa: BLE001
                log.warning("heatmap_second_pass.persist_failed err=%s", e)
                continue
    if persisted:
        await publish(
            case_id,
            {
                "type": "heatmap_second_pass",
                "case_id": str(case_id),
                "data": {
                    "centroid_lat": lat,
                    "centroid_lon": lon,
                    "hotspots_added": persisted,
                },
            },
        )
