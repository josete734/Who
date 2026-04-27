"""Unit tests for the EU registries (OpenCorporates) collector.

The OpenCorporates HTTP endpoint is mocked via ``respx`` so the test runs
fully offline.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.eu_registries import EURegistriesCollector
from app.schemas import SearchInput


def _officers_payload() -> dict:
    return {
        "results": {
            "officers": [
                {
                    "officer": {
                        "name": "Jane Doe",
                        "position": "director",
                        "jurisdiction_code": "gb",
                        "start_date": "2018-01-01",
                        "end_date": None,
                        "opencorporates_url": "https://opencorporates.com/officers/123",
                        "company": {
                            "name": "ACME LTD",
                            "company_number": "01234567",
                            "jurisdiction_code": "gb",
                        },
                    }
                },
                {
                    "officer": {
                        "name": "Jane Doe",
                        "position": "administrador único",
                        "jurisdiction_code": "es",
                        "start_date": "2020-05-10",
                        "end_date": None,
                        "opencorporates_url": "https://opencorporates.com/officers/456",
                        "company": {
                            "name": "ACME IBERIA SL",
                            "company_number": "B12345678",
                            "jurisdiction_code": "es",
                        },
                    }
                },
            ]
        }
    }


@pytest.mark.asyncio
async def test_eu_registries_yields_officer_findings(monkeypatch):
    monkeypatch.delenv("OPENCORPORATES_API_KEY", raising=False)

    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r"https://api\.opencorporates\.com/v0\.4/officers/search.*").mock(
            return_value=httpx.Response(200, json=_officers_payload())
        )

        collector = EURegistriesCollector()
        # Speed up free-tier pacing for the test.
        async def _no_sleep(*_a, **_k):
            return None
        monkeypatch.setattr("app.collectors.eu_registries.jitter_sleep", _no_sleep)

        findings = []
        async for f in collector.run(SearchInput(full_name="Jane Doe")):
            findings.append(f)

        assert route.called

    assert len(findings) == 2
    f = findings[0]
    assert f.collector == "eu_registries"
    assert f.category == "eu_official"
    assert f.entity_type == "OfficerRecord"
    assert f.payload["kind"] == "officer_record"
    assert f.payload["evidence"]["jurisdiction"] in {"gb", "es"}
    assert f.payload["evidence"]["source"] == "opencorporates"
    assert f.payload["authenticated"] is False
    juris = {x.payload["evidence"]["jurisdiction"] for x in findings}
    assert juris == {"gb", "es"}


@pytest.mark.asyncio
async def test_eu_registries_uses_api_key_when_set(monkeypatch):
    monkeypatch.setenv("OPENCORPORATES_API_KEY", "secret-token")

    captured: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={"results": {"officers": []}})

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.opencorporates\.com/v0\.4/officers/search.*").mock(
            side_effect=_handler
        )
        collector = EURegistriesCollector()
        findings = [f async for f in collector.run(SearchInput(full_name="Jane Doe"))]

    assert findings == []
    assert captured, "expected at least one HTTP call"
    assert "api_token=secret-token" in captured[0]
    assert "jurisdiction_code=es%2Cgb%2Cfr%2Cde%2Cit%2Cpt" in captured[0]


@pytest.mark.asyncio
async def test_eu_registries_skips_when_no_terms():
    collector = EURegistriesCollector()
    findings = [f async for f in collector.run(SearchInput())]
    assert findings == []


@pytest.mark.asyncio
async def test_eu_registries_handles_non_200(monkeypatch):
    monkeypatch.delenv("OPENCORPORATES_API_KEY", raising=False)

    async def _no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr("app.collectors.eu_registries.jitter_sleep", _no_sleep)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.opencorporates\.com/.*").mock(
            return_value=httpx.Response(429, text="rate limited")
        )
        collector = EURegistriesCollector()
        findings = [f async for f in collector.run(SearchInput(full_name="Jane Doe"))]

    assert findings == []


def test_collector_is_registered():
    from app.collectors.base import collector_registry

    assert collector_registry.by_name("eu_registries") is EURegistriesCollector
