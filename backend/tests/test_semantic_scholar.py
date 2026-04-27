"""Tests for the Semantic Scholar author search collector."""
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
async def test_semantic_scholar_yields_authors(monkeypatch, patch_get_client):
    from app.collectors import semantic_scholar as mod

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    patch_get_client(mod)

    payload = {
        "data": [
            {
                "authorId": "1234",
                "name": "Jane Doe",
                "affiliations": ["MIT"],
                "paperCount": 42,
            }
        ]
    }

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.semanticscholar\.org/graph/v1/author/search.*").mock(
            return_value=httpx.Response(200, json=payload)
        )
        findings = [
            f
            async for f in mod.SemanticScholarCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "semantic_scholar"
    assert f.category == "academic"
    assert f.entity_type == "Author"
    assert f.payload["evidence"]["author_id"] == "1234"
    assert f.payload["evidence"]["paper_count"] == 42
    assert f.payload["authenticated"] is False
    assert f.url == "https://www.semanticscholar.org/author/1234"


@pytest.mark.asyncio
async def test_semantic_scholar_handles_429(monkeypatch, patch_get_client):
    from app.collectors import semantic_scholar as mod

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
            return_value=httpx.Response(429, text="slow down")
        )
        findings = [
            f
            async for f in mod.SemanticScholarCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_semantic_scholar_uses_api_key(monkeypatch, patch_get_client):
    from app.collectors import semantic_scholar as mod

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "abc123")
    patch_get_client(mod)

    captured: list[httpx.Headers] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers)
        return httpx.Response(200, json={"data": []})

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
            side_effect=_handler
        )
        findings = [
            f
            async for f in mod.SemanticScholarCollector().run(
                SearchInput(full_name="Jane Doe")
            )
        ]

    assert findings == []
    assert captured and captured[0].get("x-api-key") == "abc123"
