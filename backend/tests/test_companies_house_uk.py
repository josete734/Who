"""Tests for the Companies House UK collector (mocked via respx)."""
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
async def test_companies_house_uk_yields_company_with_directors(patch_get_client):
    from app.collectors import companies_house_uk as mod

    patch_get_client(mod)

    search_payload = {
        "items": [
            {
                "company_number": "01234567",
                "title": "ACME LTD",
                "company_status": "active",
                "address_snippet": "1 High St, London",
                "date_of_creation": "2010-05-01",
            }
        ]
    }
    officers_payload = {
        "items": [
            {
                "name": "Doe, Jane",
                "officer_role": "director",
                "appointed_on": "2018-01-01",
                "resigned_on": None,
                "nationality": "British",
            }
        ]
    }

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.company-information\.service\.gov\.uk/search/companies.*").mock(
            return_value=httpx.Response(200, json=search_payload)
        )
        router.get(
            url__regex=r"https://api\.company-information\.service\.gov\.uk/company/01234567/officers"
        ).mock(return_value=httpx.Response(200, json=officers_payload))

        findings = [
            f
            async for f in mod.CompaniesHouseUKCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "companies_house_uk"
    assert f.category == "registry"
    assert f.entity_type == "Company"
    assert f.payload["evidence"]["company_number"] == "01234567"
    assert f.payload["evidence"]["directors"][0]["name"] == "Doe, Jane"
    assert f.confidence == 0.7


@pytest.mark.asyncio
async def test_companies_house_uk_handles_429(patch_get_client):
    from app.collectors import companies_house_uk as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.company-information\.service\.gov\.uk/.*").mock(
            return_value=httpx.Response(429, text="rate limited")
        )
        findings = [
            f
            async for f in mod.CompaniesHouseUKCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_companies_house_uk_skips_when_no_terms():
    from app.collectors import companies_house_uk as mod

    findings = [
        f async for f in mod.CompaniesHouseUKCollector().run(SearchInput())
    ]
    assert findings == []
