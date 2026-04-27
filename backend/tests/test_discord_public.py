"""Tests for the Discord public lookup collector (Wave 3 / C4)."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.collectors.discord_public import (
    DISCORD_EPOCH_MS,
    DiscordPublicCollector,
    snowflake_to_datetime,
)
from app.schemas import SearchInput


# A real-shape Discord snowflake. Bits[63..22] = ms since DISCORD_EPOCH_MS.
# We construct one for 2021-09-09T03:24:00Z deterministically below.
_KNOWN_DT = dt.datetime(2021, 9, 9, 3, 24, 0, tzinfo=dt.timezone.utc)
_KNOWN_SNOWFLAKE = ((int(_KNOWN_DT.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22) | 0


def test_snowflake_to_datetime_round_trip():
    decoded = snowflake_to_datetime(_KNOWN_SNOWFLAKE)
    assert decoded == _KNOWN_DT


def test_snowflake_to_datetime_accepts_string():
    decoded = snowflake_to_datetime(str(_KNOWN_SNOWFLAKE))
    assert decoded.tzinfo is not None
    assert decoded == _KNOWN_DT


def test_snowflake_to_datetime_known_id():
    # Public Discord example: 175928847299117063 -> 2016-04-30T11:18:25.796Z
    decoded = snowflake_to_datetime(175928847299117063)
    assert decoded.year == 2016
    assert decoded.month == 4
    assert decoded.day == 30


class _MockResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _MockClient:
    def __init__(self, response: _MockResponse):
        self._response = response

    async def __aenter__(self) -> "_MockClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, *args: Any, **kwargs: Any) -> _MockResponse:
        self.last_url = url
        return self._response


@pytest.mark.asyncio
async def test_discord_public_lookup_by_id_emits_finding():
    snowflake = str(_KNOWN_SNOWFLAKE)
    payload = {
        "id": snowflake,
        "username": "testuser",
        "avatar_url": "https://cdn.discord/x.png",
        "banner_url": "https://cdn.discord/b.png",
        "badges": ["HypeSquadBravery", "EarlySupporter"],
    }
    response = _MockResponse(200, payload)

    with patch(
        "app.collectors.discord_public.client",
        return_value=_MockClient(response),
    ):
        collector = DiscordPublicCollector()
        si = SearchInput(extra_context=f"discord_id={snowflake}")
        findings = [f async for f in collector.run(si)]

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "discord_public"
    assert f.entity_type == "DiscordProfile"
    assert f.url == f"https://discord.com/users/{snowflake}"
    assert f.payload["discord_id"] == snowflake
    assert f.payload["username"] == "testuser"
    assert f.payload["avatar_url"] == "https://cdn.discord/x.png"
    assert f.payload["banner"] == "https://cdn.discord/b.png"
    assert f.payload["badges"] == ["HypeSquadBravery", "EarlySupporter"]
    assert f.payload["created_at_estimate"] == _KNOWN_DT.isoformat()


@pytest.mark.asyncio
async def test_discord_public_username_only_returns_empty():
    # No public unauthenticated username->profile resolver: must yield nothing.
    collector = DiscordPublicCollector()
    si = SearchInput(username="someone")
    findings = [f async for f in collector.run(si)]
    assert findings == []


@pytest.mark.asyncio
async def test_discord_public_handles_http_error_gracefully():
    class _ErrClient(_MockClient):
        async def get(self, *a: Any, **kw: Any) -> _MockResponse:
            raise httpx.ConnectError("boom")

    with patch(
        "app.collectors.discord_public.client",
        return_value=_ErrClient(_MockResponse(0, {})),
    ):
        collector = DiscordPublicCollector()
        si = SearchInput(extra_context=f"discord_id={_KNOWN_SNOWFLAKE}")
        findings = [f async for f in collector.run(si)]
    assert findings == []


@pytest.mark.asyncio
async def test_discord_public_non_200_yields_nothing():
    response = _MockResponse(404, {})
    with patch(
        "app.collectors.discord_public.client",
        return_value=_MockClient(response),
    ):
        collector = DiscordPublicCollector()
        si = SearchInput(extra_context=f"discord_id={_KNOWN_SNOWFLAKE}")
        findings = [f async for f in collector.run(si)]
    assert findings == []
