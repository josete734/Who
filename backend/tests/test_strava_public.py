"""Tests for the Strava public collector (Wave 1 / A1.1)."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors import strava_public as strava_mod
from app.collectors.strava_public import StravaPublicCollector
from app.schemas import SearchInput


@pytest.fixture(autouse=True)
def _patch_get_client(monkeypatch):
    async def _fake_get_client(_policy="default"):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(strava_mod, "get_client", _fake_get_client)


_PROFILE_HTML = """
<html><head>
  <meta property="og:title" content="Jose Runner" />
  <meta property="og:url" content="https://www.strava.com/athletes/12345" />
  <meta property="og:image" content="https://dgalywyr863hv.cloudfront.net/pictures/athletes/12345/photo.jpg" />
</head><body>
  <div class="location">Madrid, Spain</div>
  <a href="/athletes/12345/followers">128 Followers</a>
  <a href="/athletes/12345/following">64 Following</a>
  <a href="/clubs/9876">Club Madrid</a>
  <table>
    <tr data-polyline="abc_encoded_polyline_xyz">
      <td><a href="/activities/55555">Morning Ride</a></td>
    </tr>
    <tr>
      <td><a href="/activities/66666">Evening Run</a></td>
    </tr>
  </table>
</body></html>
"""

_PROFILE_HTML_BY_USERNAME = """
<html><head>
  <meta property="og:url" content="https://www.strava.com/athletes/12345" />
  <meta property="og:title" content="Jose Runner" />
</head><body>
  <a href="/athletes/12345/followers">10 Followers</a>
</body></html>
"""

_ROUTES_HTML = """
<html><body>
  <table>
    <tr>
      <td><a href="/routes/777">Sierra Loop</a></td>
      <td>42.5 km</td>
      <td>Ride</td>
    </tr>
    <tr>
      <td><a href="/routes/888">Park Run</a></td>
      <td>10,2 km</td>
      <td>Run</td>
    </tr>
  </table>
</body></html>
"""


@pytest.mark.asyncio
async def test_strava_public_resolves_via_extra_context_id():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.strava.com/athletes/12345").mock(
            return_value=httpx.Response(200, text=_PROFILE_HTML)
        )
        router.get("https://www.strava.com/athletes/12345/routes").mock(
            return_value=httpx.Response(200, text=_ROUTES_HTML)
        )
        findings = [
            f
            async for f in StravaPublicCollector().run(
                SearchInput(extra_context="strava_athlete_id=12345")
            )
        ]

    assert findings, "expected at least one finding"
    accounts = [f for f in findings if f.entity_type == "account"]
    activities = [f for f in findings if f.entity_type == "activity"]
    routes = [f for f in findings if f.entity_type == "route"]

    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.collector == "strava_public"
    assert acc.category == "sport"
    assert acc.confidence == 0.8
    assert acc.payload["platform"] == "strava"
    assert acc.payload["athlete_id"] == "12345"
    assert acc.payload["display_name"] == "Jose Runner"
    assert acc.payload["club_ids"] == ["9876"]
    assert acc.payload["follower_count"] == 128
    assert acc.payload["follow_count"] == 64
    assert acc.payload["hometown"].startswith("Madrid")

    # Activities — at least the one with a polyline must carry it.
    polylined = [a for a in activities if a.payload.get("polyline")]
    assert polylined, "expected at least one activity with a visible polyline"
    assert polylined[0].payload["polyline"] == "abc_encoded_polyline_xyz"
    assert polylined[0].payload["activity_id"] == "55555"

    # Routes parsed.
    assert {r.payload["route_id"] for r in routes} == {"777", "888"}
    sierra = next(r for r in routes if r.payload["route_id"] == "777")
    assert sierra.payload["distance_km"] == 42.5
    assert sierra.payload["type"] == "ride"
    park = next(r for r in routes if r.payload["route_id"] == "888")
    assert park.payload["distance_km"] == 10.2


@pytest.mark.asyncio
async def test_strava_public_resolves_athlete_id_from_username():
    with respx.mock(assert_all_called=False) as router:
        # First fetch by slug returns the canonical profile (with og:url athlete id).
        router.get("https://www.strava.com/athletes/joserunner").mock(
            return_value=httpx.Response(200, text=_PROFILE_HTML_BY_USERNAME)
        )
        router.get("https://www.strava.com/athletes/12345/routes").mock(
            return_value=httpx.Response(200, text=_ROUTES_HTML)
        )
        findings = [
            f
            async for f in StravaPublicCollector().run(SearchInput(username="joserunner"))
        ]

    accounts = [f for f in findings if f.entity_type == "account"]
    assert len(accounts) == 1
    assert accounts[0].payload["athlete_id"] == "12345"
    assert accounts[0].payload["username"] == "joserunner"
    # Resolved match → high confidence.
    assert accounts[0].confidence == 0.8


@pytest.mark.asyncio
async def test_strava_public_silent_on_404():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.strava.com/athletes/ghost").mock(
            return_value=httpx.Response(404, text="not found")
        )
        findings = [
            f async for f in StravaPublicCollector().run(SearchInput(username="ghost"))
        ]
    assert findings == []
