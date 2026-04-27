"""Tests for the SEC EDGAR full-text search collector."""
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
async def test_sec_edgar_yields_filings(patch_get_client):
    from app.collectors import sec_edgar as mod

    patch_get_client(mod)

    payload = {
        "hits": {
            "hits": [
                {
                    "_id": "0001234567-23-000001",
                    "_source": {
                        "adsh": "0001234567-23-000001",
                        "ciks": ["0000320193"],
                        "form": "10-K",
                        "file_date": "2023-11-01",
                        "display_names": ["APPLE INC (0000320193) (Filer)"],
                    },
                }
            ]
        }
    }

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://efts\.sec\.gov/LATEST/search-index.*").mock(
            return_value=httpx.Response(200, json=payload)
        )
        findings = [
            f
            async for f in mod.SecEdgarCollector().run(
                SearchInput(full_name="Tim Cook")
            )
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "sec_edgar"
    assert f.category == "registry"
    assert f.entity_type == "Filing"
    assert f.payload["evidence"]["form"] == "10-K"
    assert f.payload["evidence"]["adsh"] == "0001234567-23-000001"
    assert f.confidence == 0.7


@pytest.mark.asyncio
async def test_sec_edgar_handles_500(patch_get_client):
    from app.collectors import sec_edgar as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://efts\.sec\.gov/.*").mock(
            return_value=httpx.Response(503, text="oops")
        )
        findings = [
            f
            async for f in mod.SecEdgarCollector().run(
                SearchInput(full_name="Tim Cook")
            )
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_sec_edgar_skips_when_no_terms():
    from app.collectors import sec_edgar as mod

    findings = [f async for f in mod.SecEdgarCollector().run(SearchInput())]
    assert findings == []
