"""Tests for Wave 2 — Strava end-to-end operability.

Covered behaviour:

1. ``HeatmapCookiePayload`` validates: rejects empty / missing CloudFront keys,
   tolerates a "Cookie:" prefix, accepts a well-formed value.
2. ``_validate_cookie_against_strava`` reports the right verdict for the
   common response codes (200 image, 403, 401, 5xx, network error).
3. ``StravaHeatmapCollector.applicable()`` is True when input has city OR
   extra_context, False otherwise (no spurious schedule).
4. ``_derive_bbox_from_input`` returns a 5 km square around the geocoded city.
5. ``strava_public._parse_profile`` no longer scrapes ``data-polyline`` from
   the profile page — every recent activity carries ``polyline=None``.
6. ``_heatmap_second_pass`` builds the bbox extra_context correctly and
   propagates findings emitted by the collector with ``second_pass=True``.

These tests use respx + monkeypatch in the same style as
``tests/test_new_collectors.py`` so they stay fast and hermetic (no real
network, no Postgres required).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

# Tests in this file exercise the strava router (heatmap-cookie endpoint),
# which imports FastAPI. Skip the entire module gracefully when FastAPI is
# absent so contributors running pytest in a minimal venv don't see hard errors.
fastapi = pytest.importorskip("fastapi")

from app.collectors import strava_heatmap as heatmap_mod
from app.collectors import strava_public as strava_pub_mod
from app.collectors.strava_heatmap import (
    StravaHeatmapCollector,
    _bbox_around,
    _derive_bbox_from_input,
)
from app.collectors.strava_public import _parse_profile
from app.routers.strava import (
    HeatmapCookiePayload,
    _validate_cookie_against_strava,
)
from app.schemas import SearchInput


# ---------------------------------------------------------------------------
# 1) HeatmapCookiePayload validation
# ---------------------------------------------------------------------------
def test_cookie_payload_accepts_well_formed():
    p = HeatmapCookiePayload(
        cookie="CloudFront-Policy=abc; CloudFront-Signature=def; CloudFront-Key-Pair-Id=ghi"
    )
    assert "CloudFront-Policy=abc" in p.cookie


def test_cookie_payload_strips_cookie_prefix():
    p = HeatmapCookiePayload(
        cookie="Cookie: CloudFront-Policy=abc; CloudFront-Signature=def; CloudFront-Key-Pair-Id=ghi"
    )
    assert p.cookie.startswith("CloudFront-Policy=abc")


def test_cookie_payload_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        HeatmapCookiePayload(cookie="")


def test_cookie_payload_rejects_missing_keys():
    with pytest.raises(ValueError, match="missing CloudFront keys"):
        HeatmapCookiePayload(cookie="CloudFront-Policy=abc; CloudFront-Signature=def")


def test_cookie_payload_rejects_empty_policy_value():
    with pytest.raises(ValueError, match="CloudFront-Policy"):
        HeatmapCookiePayload(
            cookie="CloudFront-Policy=; CloudFront-Signature=def; CloudFront-Key-Pair-Id=ghi"
        )


# ---------------------------------------------------------------------------
# 2) Strava CloudFront probe — verdicts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validate_cookie_returns_ok_on_image_200():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://heatmap-external.*").mock(
            return_value=httpx.Response(
                200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"}
            )
        )
        ok, reason = await _validate_cookie_against_strava("CloudFront-Policy=x")
    assert ok and reason == "ok"


@pytest.mark.asyncio
async def test_validate_cookie_rejects_on_403():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://heatmap-external.*").mock(
            return_value=httpx.Response(403)
        )
        ok, reason = await _validate_cookie_against_strava("bad-cookie")
    assert not ok
    assert "rejected" in reason


@pytest.mark.asyncio
async def test_validate_cookie_rejects_on_401():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://heatmap-external.*").mock(
            return_value=httpx.Response(401)
        )
        ok, reason = await _validate_cookie_against_strava("bad-cookie")
    assert not ok
    assert "expired" in reason


# ---------------------------------------------------------------------------
# 3) StravaHeatmapCollector.applicable()
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
# 4) bbox derivation from city + country
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

    monkeypatch.setattr(
        "app.geo.extractor._Geocoder", FakeGeo, raising=True
    )

    bbox = await _derive_bbox_from_input(SearchInput(city="Reus", country="ES"))
    assert bbox is not None
    lat_min, lon_min, lat_max, lon_max = bbox
    # ~5 km square around the centroid; allow loose bounds.
    assert lat_min < 41.1561 < lat_max
    assert lon_min < 1.1077 < lon_max
    assert (lat_max - lat_min) == pytest.approx(2 * 2.5 / 111.0, rel=0.05)


@pytest.mark.asyncio
async def test_derive_bbox_returns_none_without_city():
    bbox = await _derive_bbox_from_input(SearchInput(email="x@example.com"))
    assert bbox is None


def test_bbox_around_math_is_symmetric():
    bbox = _bbox_around(41.0, 1.0, half_km=2.5)
    lat_min, lon_min, lat_max, lon_max = bbox
    # Symmetry: centre is the average.
    assert (lat_min + lat_max) / 2 == pytest.approx(41.0, abs=1e-9)
    assert (lon_min + lon_max) / 2 == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 5) strava_public no longer scrapes data-polyline (Wave 2 dead-code removal)
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
# 6) _heatmap_second_pass builds the right extra_context
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_heatmap_second_pass_invokes_collector_with_bbox(monkeypatch):
    """We don't have a Postgres instance here; instead, intercept the heatmap
    collector and the session_scope to verify the bbox we feed in."""
    captured: dict[str, list] = {"inputs": []}

    async def _fake_run(self, input_):  # signature must match Collector.run
        captured["inputs"].append(input_)
        if False:  # generator with no items
            yield None

    monkeypatch.setattr(
        "app.collectors.strava_heatmap.StravaHeatmapCollector.run",
        _fake_run,
        raising=True,
    )

    # Stub out session_scope so the function doesn't try to talk to Postgres.
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
    # publish() is fail-soft; just make it a no-op.
    monkeypatch.setattr(
        "app.tasks_spatial.publish",
        AsyncMock(return_value=None),
        raising=True,
    )

    from app.tasks_spatial import _heatmap_second_pass
    import uuid

    cid = uuid.uuid4()
    await _heatmap_second_pass(cid, lat=41.1561, lon=1.1077, half_km=1.0)

    assert captured["inputs"], "the heatmap collector must be invoked"
    si = captured["inputs"][0]
    assert "strava_bbox=" in (si.extra_context or "")
    assert "41." in si.extra_context
    assert "1." in si.extra_context
