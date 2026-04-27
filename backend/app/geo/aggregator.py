"""H3-hex heatmap aggregator.

Loads ``geo_signals`` for a case, projects each onto an H3 hex at the
requested resolution (5..7), and returns weighted aggregates suitable for
the UI heatmap layer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

try:
    import h3  # type: ignore
    _H3_OK = True
except Exception:  # pragma: no cover
    h3 = None  # type: ignore
    _H3_OK = False


@dataclass(slots=True)
class HexAggregate:
    h3: str
    lat: float
    lon: float
    weight: float
    count: int
    kinds: dict[str, int] = field(default_factory=dict)
    collectors: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# H3 helpers — tolerate both h3-py v3 and v4 APIs
# ---------------------------------------------------------------------------
def _latlon_to_cell(lat: float, lon: float, res: int) -> str:
    if not _H3_OK:
        # Pseudo-cell fallback: bucket by ~1deg grid; tests can still assert
        # equality of two nearby points without the C lib present.
        gx = round(lat * (10 ** (res - 4)))
        gy = round(lon * (10 ** (res - 4)))
        return f"fake-{res}-{gx}-{gy}"
    if hasattr(h3, "latlng_to_cell"):  # v4
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)  # v3


def _cell_to_latlon(cell: str) -> tuple[float, float]:
    if not _H3_OK:
        try:
            _, _, gx, gy = cell.split("-")
            # cell encodes res into the second token
            res = int(cell.split("-")[1])
            return float(gx) / (10 ** (res - 4)), float(gy) / (10 ** (res - 4))
        except Exception:
            return 0.0, 0.0
    if hasattr(h3, "cell_to_latlng"):  # v4
        return h3.cell_to_latlng(cell)
    return h3.h3_to_geo(cell)  # v3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _signal_weight(confidence: float, accuracy_km: float, kind: str) -> float:
    """Weight a signal: higher confidence / smaller error / direct kinds win.

    A 'tz_hint' is intentionally squashed because its accuracy is country
    scale; it should *suggest*, never *dominate*.
    """
    base = max(0.05, min(1.0, float(confidence)))
    # Inverse-accuracy taper: a 1km signal is 1.0; a 500km tz hint ~0.05.
    taper = 1.0 / (1.0 + (max(0.5, accuracy_km) / 25.0))
    kind_factor = {
        "ip": 0.9,
        "address": 1.0,
        "social_place": 0.8,
        "tz_hint": 0.2,
        "exif": 1.0,
    }.get(kind, 0.6)
    return base * taper * kind_factor


def aggregate(signals: Iterable[dict[str, Any]], *, h3_res: int = 6) -> list[HexAggregate]:
    """Pure aggregation step (no DB access). Used by tests + ``build_heatmap``."""
    if h3_res < 5 or h3_res > 7:
        raise ValueError("h3_res must be in 5..7")
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"weight": 0.0, "count": 0, "kinds": defaultdict(int), "collectors": defaultdict(int)}
    )
    for s in signals:
        try:
            lat = float(s["lat"])
            lon = float(s["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        cell = _latlon_to_cell(lat, lon, h3_res)
        b = buckets[cell]
        b["weight"] += _signal_weight(
            float(s.get("confidence", 0.5)),
            float(s.get("accuracy_km", 25.0)),
            str(s.get("kind", "")),
        )
        b["count"] += 1
        b["kinds"][str(s.get("kind", "?"))] += 1
        b["collectors"][str(s.get("source_collector", "?"))] += 1

    out: list[HexAggregate] = []
    for cell, b in buckets.items():
        clat, clon = _cell_to_latlon(cell)
        out.append(
            HexAggregate(
                h3=cell,
                lat=clat,
                lon=clon,
                weight=round(b["weight"], 6),
                count=b["count"],
                kinds=dict(b["kinds"]),
                collectors=dict(b["collectors"]),
            )
        )
    out.sort(key=lambda h: h.weight, reverse=True)
    return out


async def build_heatmap(case_id: str, *, h3_res: int = 6) -> list[HexAggregate]:
    """Read ``geo_signals`` for ``case_id`` and return hex aggregates."""
    from sqlalchemy import text
    from app.db import session_scope

    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT lat, lon, accuracy_km, kind, source_collector, confidence
                      FROM geo_signals
                     WHERE case_id = :cid
                    """
                ),
                {"cid": str(case_id)},
            )
        ).mappings().all()
    return aggregate([dict(r) for r in rows], h3_res=h3_res)
