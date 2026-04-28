"""Tests for Wave 2 — Strava core fixes that do NOT depend on FastAPI.

Covers:

* ``StravaHeatmapCollector.applicable()`` accepts city OR extra_context.
* ``_derive_bbox_from_input`` geocodes city+country into a 5 km square.
* ``_bbox_around`` math is symmetric.
* ``strava_public._parse_profile`` no longer scrapes data-polyline (Wave 2
  dead-code removal).
* ``_heatmap_second_pass`` invokes the collector with the right bbox in
  ``extra_context``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.collectors.strava_heatmap import (
    StravaHeatmapCollector,
    _bbox_around,
    _derive_bbox_from_input,
)
from app.collectors.strava_public import _parse_profile
from app.schemas import SearchInput


# ---------------------------------------------------------------------------
# applicable()
# ---------------------------------------------------------------------------
def test_heatmap_applicable_with_city_only():
    c = StravaHeatmapCollector()
    assert c.applicable(SearchInput(city="Reus", country="ES")) is True


def test_heatmap_applicable_with_extra_context_only():
    c = StravaHeatmapCollector()
    assert c.applicable(SearchInput(extra_context="strava_bbox=41,1,42,2")) is True


def test_heatmap_not_applicable_with_only_email():
    c = StravaHeatmapCollector()
    assert c.applicable(SearchInput(email="x@example.com")) is False


# ---------------------------------------------------------------------------
# bbox derivation from city + country
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_derive_bbox_uses_geocoder_result(monkeypatch):
    """When the geocoder returns Reus's coordinates, we get a ~5 km square
    around it. Patch out the actual Nominatim call entirely."""

    class FakeGeo:
        @classmethod
        def get(cls):
            return cls()

        async def geocode(self, name, allow_live=False):
            assert "Reus" in name
            return (41.1561, 1.1077, 0.5)  # lat, lon, accuracy_km

    # The collector imports _Geocoder *inside* the function body, so patching
    # the location where it lives is sufficient.
    monkeypatch.setattr("app.geo.extractor._Geocoder", FakeGeo, raising=True)

    bbox = await _derive_bbox_from_input(SearchInput(city="Reus", country="ES"))
    assert bbox is not None
    lat_min, lon_min, lat_max, lon_max = bbox
    assert lat_min < 41.1561 < lat_max
    assert lon_min < 1.1077 < lon_max
    # ~5 km square around the centroid (2.5 km half-edge); allow loose bounds.
    assert (lat_max - lat_min) == pytest.approx(2 * 2.5 / 111.0, rel=0.05)


@pytest.mark.asyncio
async def test_derive_bbox_returns_none_without_city():
    bbox = await _derive_bbox_from_input(SearchInput(email="x@example.com"))
    assert bbox is None


def test_bbox_around_math_is_symmetric():
    bbox = _bbox_around(41.0, 1.0, half_km=2.5)
    lat_min, lon_min, lat_max, lon_max = bbox
    assert (lat_min + lat_max) / 2 == pytest.approx(41.0, abs=1e-9)
    assert (lon_min + lon_max) / 2 == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# strava_public no longer scrapes data-polyline
# ---------------------------------------------------------------------------
_STRAVA_PROFILE_HTML = """
<!doctype html>
<html><body>
<a href="/athletes/12345"><h1>Jane Doe</h1></a>
<table>
  <tr data-polyline="abc123fakeencoded">
    <td><a href="/activities/9999">Morning Run</a></td>
  </tr>
  <tr>
    <td><a href="/activities/8888">Evening Ride</a></td>
  </tr>
</table>
</body></html>
"""


def test_parse_profile_drops_polyline_field():
    """Even if the HTML still embeds data-polyline (which it no longer does
    in production), the new parser returns polyline=None for every activity
    so downstream code never relies on these stale values."""
    out = _parse_profile(_STRAVA_PROFILE_HTML)
    assert out["recent_activities"], "must still extract activity IDs"
    for act in out["recent_activities"]:
        assert act["polyline"] is None, f"polyline must be None, got {act['polyline']!r}"
    ids = {a["activity_id"] for a in out["recent_activities"]}
    assert {"9999", "8888"}.issubset(ids)


# ---------------------------------------------------------------------------
# _heatmap_second_pass — bbox propagation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_heatmap_second_pass_invokes_collector_with_bbox(monkeypatch):
    """Verify the function builds a strava_bbox= around (lat, lon) and feeds
    it to the collector. We stub session_scope and publish so no Postgres or
    Redis is required."""
    captured: dict = {"inputs": []}

    async def _fake_run(self, input_):  # signature must match Collector.run
        captured["inputs"].append(input_)
        if False:
            yield None  # async generator with no items

    monkeypatch.setattr(
        "app.collectors.strava_heatmap.StravaHeatmapCollector.run",
        _fake_run,
        raising=True,
    )

    class FakeSession:
        async def execute(self, *a, **kw):
            class _R:
                rowcount = 0

                def scalar_one(self):
                    return None

            return _R()

    class _Ctx:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "app.tasks_spatial.session_scope", lambda: _Ctx(), raising=True
    )
    monkeypatch.setattr(
        "app.tasks_spatial.publish",
        AsyncMock(return_value=None),
        raising=True,
    )

    from app.tasks_spatial import _heatmap_second_pass

    cid = uuid.uuid4()
    await _heatmap_second_pass(cid, lat=41.1561, lon=1.1077, half_km=1.0)

    assert captured["inputs"], "the heatmap collector must be invoked"
    si = captured["inputs"][0]
    ec = si.extra_context or ""
    assert "strava_bbox=" in ec
    # Bounds should bracket the centre lat/lon.
    nums = ec.split("strava_bbox=", 1)[1].split(",")
    lat_min, lon_min, lat_max, lon_max = (float(x) for x in nums[:4])
    assert lat_min < 41.1561 < lat_max
    assert lon_min < 1.1077 < lon_max
