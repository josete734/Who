"""Tests for global Strava tokens + hometown-based candidate ranking.

Covers:
  * ``strava_public._rank_candidates_by_city`` chooses the candidate whose
    hometown matches the case ``city`` (case-insensitive substring match).
  * Confidence is bumped to 0.95 on a hometown match, lowered to 0.4 when
    none match.
  * The ranked candidate's ``athlete_id`` is persisted on the account
    finding alongside a ``match_score`` debug dict.
"""
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


def _profile_html(athlete_id: str, hometown: str) -> str:
    return f"""
    <html><head>
      <meta property="og:url" content="https://www.strava.com/athletes/{athlete_id}" />
      <meta property="og:title" content="Athlete {athlete_id}" />
    </head><body>
      <div class="location">{hometown}</div>
    </body></html>
    """


_EMPTY_ROUTES = "<html><body></body></html>"


@pytest.mark.asyncio
async def test_searxng_dork_ranks_by_city_match(monkeypatch):
    # Mock SearXNG to return THREE candidates. Only one (44698641) has a
    # hometown matching the input city "Reus".
    async def _fake_candidates(_c, _full_name):
        return ["10000001", "44698641", "10000002"]

    monkeypatch.setattr(strava_mod, "_candidates_via_searxng", _fake_candidates)

    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.strava.com/athletes/10000001").mock(
            return_value=httpx.Response(
                200, text=_profile_html("10000001", "Tarragona, Spain")
            )
        )
        router.get("https://www.strava.com/athletes/44698641").mock(
            return_value=httpx.Response(
                200, text=_profile_html("44698641", "Reus, Spain")
            )
        )
        router.get("https://www.strava.com/athletes/10000002").mock(
            return_value=httpx.Response(
                200, text=_profile_html("10000002", "Barcelona, Spain")
            )
        )
        router.get("https://www.strava.com/athletes/44698641/routes").mock(
            return_value=httpx.Response(200, text=_EMPTY_ROUTES)
        )

        findings = [
            f
            async for f in StravaPublicCollector().run(
                SearchInput(full_name="Jose Castillo", city="Reus")
            )
        ]

    accounts = [f for f in findings if f.entity_type == "account"]
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.payload["athlete_id"] == "44698641"
    assert acc.confidence == 0.95
    assert "match_score" in acc.payload
    score = acc.payload["match_score"]
    assert score["matched"] is True
    assert score["city_query"] == "Reus"
    # Ensure the inspected list reached the matching candidate.
    inspected_ids = [c["athlete_id"] for c in score["candidates_inspected"]]
    assert "44698641" in inspected_ids


@pytest.mark.asyncio
async def test_no_hometown_match_falls_back_with_low_confidence(monkeypatch):
    async def _fake_candidates(_c, _full_name):
        return ["10000001", "10000002"]

    monkeypatch.setattr(strava_mod, "_candidates_via_searxng", _fake_candidates)

    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.strava.com/athletes/10000001").mock(
            return_value=httpx.Response(
                200, text=_profile_html("10000001", "Tarragona, Spain")
            )
        )
        router.get("https://www.strava.com/athletes/10000002").mock(
            return_value=httpx.Response(
                200, text=_profile_html("10000002", "Barcelona, Spain")
            )
        )
        router.get("https://www.strava.com/athletes/10000001/routes").mock(
            return_value=httpx.Response(200, text=_EMPTY_ROUTES)
        )

        findings = [
            f
            async for f in StravaPublicCollector().run(
                SearchInput(full_name="Jose Castillo", city="Reus")
            )
        ]

    accounts = [f for f in findings if f.entity_type == "account"]
    assert len(accounts) == 1
    acc = accounts[0]
    # Legacy fallback to first candidate.
    assert acc.payload["athlete_id"] == "10000001"
    assert acc.confidence == 0.4
    assert acc.payload["match_score"]["matched"] is False
