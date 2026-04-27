"""Triangulation of likely home/work/gym locations from activity polylines.

Implements the "Strava-stalking" pattern: cluster endpoints of multiple GPS
activities to surface candidate dwell points, with temporal-diversity gating
and lightweight kind classification.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .polyline import decode_polyline

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points in meters."""
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_R_M * c


@dataclass
class Activity:
    id: str
    polyline: str
    start_dt: datetime | None = None
    kind_hint: str | None = None


@dataclass
class InferredLocation:
    kind: str
    lat: float
    lon: float
    radius_m: int
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    source_finding_ids: list[str] = field(default_factory=list)


def _extract_endpoints(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Endpoints (start + end) plus simple pause-points (gap >150m between samples)."""
    if not coords:
        return []
    pts: list[tuple[float, float]] = [coords[0], coords[-1]]
    for i in range(1, len(coords)):
        d = haversine_m(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
        # Heuristic: a >150m jump between consecutive samples often signals
        # a pause/teleport (GPS resumed after a stop). Treat as pause point.
        if d > 150.0:
            pts.append(coords[i - 1])
            pts.append(coords[i])
    return pts


def _classify_kind(times: list[datetime]) -> str:
    if not times:
        return "inferred_route_endpoint"
    weekday_business = sum(1 for t in times if t.weekday() < 5 and 8 <= t.hour < 18)
    early_or_late = sum(1 for t in times if t.hour < 8 or t.hour >= 19)
    n = len(times)
    if early_or_late * 2 >= n:
        return "inferred_home"
    if weekday_business * 2 >= n:
        return "inferred_work"
    # Gym heuristic: same hour-of-day repeated (>=60% in single hour bucket)
    hour_counts = Counter(t.hour for t in times)
    if hour_counts:
        _, top = hour_counts.most_common(1)[0]
        if top / n >= 0.6 and n >= 3:
            return "inferred_gym"
    return "inferred_route_endpoint"


def infer_locations(
    activities: list[Activity],
    *,
    min_activities: int = 5,
    buffer_m: int = 250,
) -> list[InferredLocation]:
    """Cluster activity endpoints and emit candidate dwell locations.

    Returns inferred locations with confidence scores. Clusters are dropped if
    they don't span at least 3 distinct calendar days (temporal diversity).
    """
    try:
        from sklearn.cluster import DBSCAN  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("scikit-learn is required for triangulation") from e

    if len(activities) < min_activities:
        return []

    # Per-point: (lat, lon, activity_index)
    points: list[tuple[float, float, int]] = []
    for idx, act in enumerate(activities):
        coords = decode_polyline(act.polyline)
        for lat, lon in _extract_endpoints(coords):
            points.append((lat, lon, idx))
    if not points:
        return []

    eps_deg = buffer_m / 111_000.0
    X = [[p[0], p[1]] for p in points]
    labels = DBSCAN(eps=eps_deg, min_samples=min_activities, metric="euclidean").fit(X).labels_

    clusters: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab < 0:
            continue
        clusters[int(lab)].append(i)

    out: list[InferredLocation] = []
    for _lab, idxs in clusters.items():
        # Distinct activities in this cluster
        act_idxs = sorted({points[i][2] for i in idxs})
        if len(act_idxs) < min_activities:
            continue
        cluster_acts = [activities[i] for i in act_idxs]

        # Temporal diversity (distinct days)
        days = {a.start_dt.date() for a in cluster_acts if a.start_dt is not None}
        temporal_diversity = len(days)
        if temporal_diversity < 3:
            continue

        lats = [points[i][0] for i in idxs]
        lons = [points[i][1] for i in idxs]
        clat = sum(lats) / len(lats)
        clon = sum(lons) / len(lons)
        radius = int(max(haversine_m(clat, clon, points[i][0], points[i][1]) for i in idxs))

        times = [a.start_dt for a in cluster_acts if a.start_dt is not None]
        kind = _classify_kind(times)

        n_acts = len(act_idxs)
        confidence = min(0.95, 0.4 + 0.05 * n_acts + 0.1 * temporal_diversity)

        evidence = {
            "n_activities": n_acts,
            "n_endpoints": len(idxs),
            "temporal_diversity_days": temporal_diversity,
            "radius_m": radius,
            "buffer_m": buffer_m,
        }
        out.append(
            InferredLocation(
                kind=kind,
                lat=clat,
                lon=clon,
                radius_m=radius,
                confidence=round(confidence, 3),
                evidence=evidence,
                source_finding_ids=[a.id for a in cluster_acts],
            )
        )
    # Stable order: highest confidence first
    out.sort(key=lambda x: x.confidence, reverse=True)
    return out
