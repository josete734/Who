"""Tests for the devto and hashnode collectors (Ola 2.4)."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors import devto as devto_mod
from app.collectors import hashnode as hashnode_mod
from app.collectors.devto import DevToCollector
from app.collectors.hashnode import HashnodeCollector
from app.schemas import SearchInput


@pytest.fixture(autouse=True)
def _patch_get_client(monkeypatch):
    async def _fake_get_client(_policy="default"):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(devto_mod, "get_client", _fake_get_client)
    monkeypatch.setattr(hashnode_mod, "get_client", _fake_get_client)


_DEVTO_HTML = """
<html><head>
  <meta property="og:title" content="Alice Dev" />
  <meta name="description" content="Dev who builds things" />
</head><body>
  <p>Joined Mar 12, 2020</p>
  <p>42 posts published</p>
</body></html>
"""

_HASHNODE_HTML = """
<html><head>
  <meta property="og:title" content="Bob Writer" />
  <meta name="description" content="Writes about Postgres" />
  <meta property="og:url" content="https://hashnode.com/@bob" />
</head><body>
  <a href="https://bob.hashnode.dev">My Blog</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_devto_collector_parses_profile():
    user = "alice"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://dev.to/{user}").mock(
            return_value=httpx.Response(200, text=_DEVTO_HTML)
        )
        findings = [f async for f in DevToCollector().run(SearchInput(username=user))]
    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "devto"
    assert f.entity_type == "account"
    assert f.confidence == 0.65
    assert f.payload["name"] == "Alice Dev"
    assert f.payload["bio"] == "Dev who builds things"
    assert f.payload["joined_date"] == "Mar 12, 2020"
    assert f.payload["posts"] == "42"


@pytest.mark.asyncio
async def test_devto_collector_silent_on_404():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://dev.to/ghost").mock(
            return_value=httpx.Response(404, text="not found")
        )
        findings = [f async for f in DevToCollector().run(SearchInput(username="ghost"))]
    assert findings == []


@pytest.mark.asyncio
async def test_hashnode_collector_parses_profile():
    user = "bob"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://hashnode.com/@{user}").mock(
            return_value=httpx.Response(200, text=_HASHNODE_HTML)
        )
        findings = [f async for f in HashnodeCollector().run(SearchInput(username=user))]
    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "hashnode"
    assert f.entity_type == "account"
    assert f.category == "lifestyle"
    assert f.confidence == 0.65
    assert f.payload["name"] == "Bob Writer"
    assert f.payload["bio"] == "Writes about Postgres"
    assert f.payload["blog_url"] == "https://bob.hashnode.dev"


@pytest.mark.asyncio
async def test_hashnode_collector_silent_on_request_error():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hashnode.com/@x").mock(
            side_effect=httpx.ConnectError("boom")
        )
        findings = [f async for f in HashnodeCollector().run(SearchInput(username="x"))]
    assert findings == []
