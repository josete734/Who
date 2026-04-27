"""Tests for the CourtListener people search collector."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.schemas import SearchInput


@pytest.fixture
def patch_get_client(monkeypatch):
    def _apply(module):
        async def _fake(_policy="default"):
            return httpx.AsyncClient(follow_redirects=True)

        monkeypatch.setattr(module, "get_client", _fake)
        return module

    return _apply


@pytest.mark.asyncio
async def test_courtlistener_yields_persons(patch_get_client):
    from app.collectors import courtlistener as mod

    patch_get_client(mod)

    payload = {
        "results": [
            {
                "id": 99,
                "name_first": "Jane",
                "name_middle": "Q",
                "name_last": "Doe",
                "absolute_url": "/person/99/jane-doe/",
                "date_dob": "1970-01-01",
                "positions": ["Judge"],
                "political_affiliations": [],
            }
        ]
    }

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://www\.courtlistener\.com/api/rest/v3/people/.*").mock(
            return_value=httpx.Response(200, json=payload)
        )
        findings = [
            f
            async for f in mod.CourtListenerCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "courtlistener"
    assert f.category == "legal"
    assert f.entity_type == "Person"
    assert f.url == "https://www.courtlistener.com/person/99/jane-doe/"
    assert "Jane" in f.payload["evidence"]["name"]
    assert f.confidence == 0.7


@pytest.mark.asyncio
async def test_courtlistener_handles_429(patch_get_client):
    from app.collectors import courtlistener as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://www\.courtlistener\.com/.*").mock(
            return_value=httpx.Response(429, text="rate limited")
        )
        findings = [
            f
            async for f in mod.CourtListenerCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_courtlistener_skips_when_no_terms():
    from app.collectors import courtlistener as mod

    findings = [
        f async for f in mod.CourtListenerCollector().run(SearchInput())
    ]
    assert findings == []
