"""Tests for skype_validator collector."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.skype_validator import SKYPE_VALIDATOR_URL, SkypeValidatorCollector
from app.schemas import SearchInput


@pytest.mark.asyncio
async def test_skype_validator_taken_emits_finding():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(
            return_value=httpx.Response(200, json={"status": 1, "markup": "Username not available"})
        )
        findings = [f async for f in SkypeValidatorCollector().run(SearchInput(username="janedoe"))]

    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "SkypePresence"
    assert f.payload["exists"] is True
    assert f.payload["username"] == "janedoe"


@pytest.mark.asyncio
async def test_skype_validator_available_no_finding():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(
            return_value=httpx.Response(200, json={"status": 0, "markup": "available"})
        )
        findings = [f async for f in SkypeValidatorCollector().run(SearchInput(username="ghost"))]
    assert findings == []


@pytest.mark.asyncio
async def test_skype_validator_http_error_no_finding():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=SKYPE_VALIDATOR_URL).mock(side_effect=httpx.ConnectError("boom"))
        findings = [f async for f in SkypeValidatorCollector().run(SearchInput(username="x"))]
    assert findings == []
