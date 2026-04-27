"""Tests for Strava OAuth helpers and authenticated collector (Wave 1 / A1.2)."""
from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
import respx

from app.collectors import strava_authed as sa_mod
from app.collectors.strava_authed import StravaAuthedCollector
from app.integrations import strava_oauth
from app.integrations.strava_oauth import (
    STRAVA_AUTH_URL,
    STRAVA_TOKEN_URL,
    build_authorize_url,
    decrypt,
    encrypt,
    exchange_code,
)
from app.schemas import SearchInput


@pytest.fixture(autouse=True)
def _patch_get_client(monkeypatch):
    async def _fake_get_client(_policy="default"):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(strava_oauth, "get_client", _fake_get_client)
    monkeypatch.setattr(sa_mod, "get_client", _fake_get_client)


def test_build_authorize_url_contains_state_and_scope():
    url = build_authorize_url(
        case_id="abc-123",
        client_id="42",
        redirect_uri="https://x/cb",
    )
    assert url.startswith(STRAVA_AUTH_URL + "?")
    assert "state=abc-123" in url
    assert "client_id=42" in url
    assert "scope=read%2Cactivity%3Aread_all" in url
    assert "redirect_uri=https%3A%2F%2Fx%2Fcb" in url


def test_encrypt_decrypt_roundtrip():
    blob = encrypt("hello-world")
    assert blob != "hello-world"
    assert decrypt(blob) == "hello-world"


@pytest.mark.asyncio
async def test_exchange_code_posts_to_token_endpoint():
    payload = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_at": 1_900_000_000,
        "athlete": {
            "id": 7,
            "username": "alice",
            "firstname": "A",
            "lastname": "Lice",
            "profile": "https://x/p.jpg",
        },
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.post(STRAVA_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = await exchange_code("CODE", "cid", "secret")
    assert route.called
    assert out["access_token"] == "AT"
    assert out["athlete"]["id"] == 7


@pytest.mark.asyncio
async def test_strava_authed_emits_activities(monkeypatch):
    case_id = uuid.uuid4()
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)

    async def _fake_load_token(cid):
        assert cid == case_id
        return {
            "id": 1,
            "athlete_id": 99,
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_at": future,
        }

    monkeypatch.setattr(sa_mod, "_load_token", _fake_load_token)

    activities = [
        {
            "id": 111,
            "name": "Morning Run",
            "sport_type": "Run",
            "start_date_local": "2026-04-20T07:00:00Z",
            "distance": 5000.0,
            "moving_time": 1800,
            "map": {"summary_polyline": "abc123"},
            "start_latlng": [40.4, -3.7],
            "end_latlng": [40.41, -3.71],
            "photos": {"primary": {"urls": {"600": "https://x/p.jpg"}}},
        },
        {
            "id": 222,
            "name": "Hidden",
            "sport_type": "Ride",
            "start_date_local": "2026-04-21T07:00:00Z",
            "distance": 10000.0,
            "moving_time": 1800,
            "map": {"summary_polyline": "def456"},
            "start_latlng": None,
            "end_latlng": None,
            "photos": {},
        },
    ]

    token = sa_mod.current_case_id.set(str(case_id))
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://www.strava.com/api/v3/athlete/activities").mock(
                return_value=httpx.Response(200, json=activities)
            )
            findings = [
                f
                async for f in StravaAuthedCollector().run(
                    SearchInput(username="alice")
                )
            ]
    finally:
        sa_mod.current_case_id.reset(token)

    assert len(findings) == 2
    f0, f1 = findings
    assert f0.collector == "strava_authed"
    assert f0.entity_type == "activity"
    assert f0.payload["polyline"] == "abc123"
    assert f0.payload["photo_url"] == "https://x/p.jpg"
    assert f0.payload["privacy_zone"] is False
    assert f0.url == "https://www.strava.com/activities/111"
    assert f1.payload["privacy_zone"] is True
    assert f1.payload["start_latlng"] is None


@pytest.mark.asyncio
async def test_strava_authed_no_token_returns_empty(monkeypatch):
    async def _fake_load_token(cid):
        return None

    monkeypatch.setattr(sa_mod, "_load_token", _fake_load_token)
    token = sa_mod.current_case_id.set(str(uuid.uuid4()))
    try:
        findings = [
            f
            async for f in StravaAuthedCollector().run(SearchInput(username="alice"))
        ]
    finally:
        sa_mod.current_case_id.reset(token)
    assert findings == []


@pytest.mark.asyncio
async def test_strava_authed_no_case_id_returns_empty():
    findings = [
        f async for f in StravaAuthedCollector().run(SearchInput(username="alice"))
    ]
    assert findings == []
