"""Tests for messengers_extra collector (Wave 3 / C12).

Skype validator + ICQ HTML scrape are mocked via respx.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.messengers_extra import (
    ICQ_PROFILE_URL,
    SKYPE_VALIDATOR_URL,
    MessengersExtraCollector,
)
from app.schemas import SearchInput


_ICQ_HTML = """
<html><head>
<meta property="og:title" content="Jane Doe" />
<meta property="og:image" content="https://icq.com/img/jane.png" />
</head><body><h1>Jane Doe</h1></body></html>
"""


@pytest.mark.asyncio
async def test_messengers_extra_skype_taken_and_icq_profile():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(
            return_value=httpx.Response(200, json={"status": 1, "markup": "Username not available"})
        )
        router.get(ICQ_PROFILE_URL.format(username="janedoe")).mock(
            return_value=httpx.Response(200, text=_ICQ_HTML)
        )

        collector = MessengersExtraCollector()
        findings = [f async for f in collector.run(SearchInput(username="janedoe"))]

    platforms = {f.payload.get("platform") for f in findings}
    assert "skype" in platforms
    assert "icq" in platforms

    skype = next(f for f in findings if f.payload["platform"] == "skype")
    assert skype.collector == "messengers_extra"
    assert skype.entity_type == "MessengerAccountExists"
    assert skype.payload["messenger_account_exists"] is True
    assert skype.confidence <= 0.5

    icq = next(f for f in findings if f.payload["platform"] == "icq")
    assert icq.payload["display_name"] == "Jane Doe"
    assert icq.payload["avatar_url"] == "https://icq.com/img/jane.png"
    assert icq.confidence <= 0.5


@pytest.mark.asyncio
async def test_messengers_extra_skype_available_no_finding():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(
            return_value=httpx.Response(200, json={"status": 0, "markup": "available"})
        )
        router.get(ICQ_PROFILE_URL.format(username="ghosthandle")).mock(
            return_value=httpx.Response(404, text="")
        )

        collector = MessengersExtraCollector()
        findings = [f async for f in collector.run(SearchInput(username="ghosthandle"))]

    assert findings == []


@pytest.mark.asyncio
async def test_messengers_extra_no_username_yields_nothing():
    collector = MessengersExtraCollector()
    findings = [f async for f in collector.run(SearchInput(phone="+34600000000"))]
    assert findings == []


@pytest.mark.asyncio
async def test_messengers_extra_handles_http_errors():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(
            side_effect=httpx.ConnectError("boom")
        )
        router.get(ICQ_PROFILE_URL.format(username="erroruser")).mock(
            side_effect=httpx.ConnectError("boom")
        )

        collector = MessengersExtraCollector()
        findings = [f async for f in collector.run(SearchInput(username="erroruser"))]

    assert findings == []
