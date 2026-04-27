"""Tests for app.spatial: polyline decoder + location triangulation."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from app.spatial import (
    Activity,
    decode_polyline,
    haversine_m,
    infer_locations,
)
from app.spatial.polyline import decode_polyline as _decode

try:  # scikit-learn is only required for the triangulation tests.
    import sklearn  # type: ignore  # noqa: F401

    _HAVE_SKLEARN = True
except ImportError:
    _HAVE_SKLEARN = False

needs_sklearn = pytest.mark.skipif(not _HAVE_SKLEARN, reason="scikit-learn not installed")


# ---------------------------------------------------------------------------
# Polyline decoder
# ---------------------------------------------------------------------------

def test_decode_polyline_known_vector():
    """Google's canonical example from the polyline algorithm docs."""
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    pts = decode_polyline(encoded)
    expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert len(pts) == len(expected)
    for got, exp in zip(pts, expected):
        assert got[0] == pytest.approx(exp[0], abs=1e-5)
        assert got[1] == pytest.approx(exp[1], abs=1e-5)


def test_decode_polyline_empty():
    assert _decode("") == []


# ---------------------------------------------------------------------------
# Helpers to synthesize polylines
# ---------------------------------------------------------------------------

def _encode_signed(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chunks = []
    while value >= 0x20:
        chunks.append((0x20 | (value & 0x1F)) + 63)
        value >>= 5
    chunks.append(value + 63)
    return "".join(chr(c) for c in chunks)


def _encode_polyline(points: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10 ** precision
    out = []
    plat = plon = 0
    for lat, lon in points:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        out.append(_encode_signed(ilat - plat))
        out.append(_encode_signed(ilon - plon))
        plat, plon = ilat, ilon
    return "".join(out)


def test_encode_decode_roundtrip():
    pts = [(40.4168, -3.7038), (40.4170, -3.7040), (40.4200, -3.7100)]
    s = _encode_polyline(pts)
    back = decode_polyline(s)
    for a, b in zip(pts, back):
        assert a[0] == pytest.approx(b[0], abs=1e-5)
        assert a[1] == pytest.approx(b[1], abs=1e-5)


# ---------------------------------------------------------------------------
# Triangulation
# ---------------------------------------------------------------------------

HOME = (40.4168, -3.7038)  # Madrid


def _route_from(home: tuple[float, float], dx_km: float = 2.0) -> str:
    """Build a tiny encoded route starting at `home` and ending ~dx_km away."""
    # ~0.009 deg lat per km; use lon offset for variety
    lat0, lon0 = home
    end = (lat0 + 0.009 * dx_km, lon0 + 0.012 * dx_km)
    return _encode_polyline([home, ((lat0 + end[0]) / 2, (lon0 + end[1]) / 2), end])


@needs_sklearn
def test_infer_locations_finds_home_with_outlier():
    base = datetime(2026, 4, 1, 6, 30)  # early morning => home
    acts: list[Activity] = []
    for i in range(10):
        acts.append(
            Activity(
                id=f"f-{i}",
                polyline=_route_from(HOME, dx_km=1.5 + (i % 4) * 0.3),
                start_dt=base + timedelta(days=i, minutes=i * 7),
            )
        )
    # Outlier in a far away city, only one activity (won't form a cluster).
    acts.append(
        Activity(
            id="f-out",
            polyline=_encode_polyline([(48.8566, 2.3522), (48.8600, 2.3600)]),
            start_dt=base + timedelta(days=2),
        )
    )

    locs = infer_locations(acts, min_activities=5, buffer_m=250)
    assert locs, "expected at least one inferred location"
    home_locs = [l for l in locs if l.kind == "inferred_home"]
    assert home_locs, f"expected an inferred_home; got kinds={[l.kind for l in locs]}"
    h = home_locs[0]
    assert haversine_m(h.lat, h.lon, HOME[0], HOME[1]) < 50.0
    assert h.radius_m <= 300
    assert h.confidence > 0.6
    assert len(h.source_finding_ids) >= 5
    assert h.evidence["n_activities"] >= 5


@needs_sklearn
def test_infer_locations_below_min_activities_returns_empty():
    base = datetime(2026, 4, 1, 6, 30)
    acts = [
        Activity(id=f"f-{i}", polyline=_route_from(HOME), start_dt=base + timedelta(days=i))
        for i in range(3)
    ]
    assert infer_locations(acts, min_activities=5) == []


@needs_sklearn
def test_infer_locations_temporal_diversity_filter():
    """All activities on the same calendar day => discarded (need >=3 days)."""
    same_day = datetime(2026, 4, 1, 6, 30)
    acts = [
        Activity(
            id=f"f-{i}",
            polyline=_route_from(HOME, dx_km=1.0 + i * 0.1),
            start_dt=same_day + timedelta(minutes=i * 11),
        )
        for i in range(10)
    ]
    locs = infer_locations(acts, min_activities=5, buffer_m=250)
    assert locs == []


def test_haversine_m_basic():
    # ~111 km per degree of latitude
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert math.isclose(d, 111_195, rel_tol=0.01)
