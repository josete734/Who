"""Tests for the WhatsMyName collector.

The wmn-data.json fetch and per-site probes are mocked via ``respx`` so
the tests run offline.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors import whatsmyname as wmn_mod
from app.collectors.whatsmyname import WMN_URL, WhatsMyNameCollector, _matches
from app.schemas import SearchInput


SAMPLE_DATA = {
    "sites": [
        {
            "name": "GitHub",
            "uri_check": "https://github.com/{account}",
            "e_code": 200,
            "e_string": "Repositories",
            "m_code": 404,
            "m_string": "Not Found",
            "cat": "coding",
        },
        {
            "name": "Reddit",
            "uri_check": "https://www.reddit.com/user/{account}",
            "e_code": 200,
            "e_string": "trophy case",
            "m_code": 404,
            "m_string": "page not found",
            "cat": "social",
        },
        {
            "name": "FakeSite",
            "uri_check": "https://fakesite.example/u/{account}",
            "e_code": 200,
            "e_string": "Profile of",
            "m_code": 404,
            "m_string": "no such user",
            "cat": "misc",
        },
    ]
}


@pytest.fixture(autouse=True)
def _bypass_cache_and_netfetch(monkeypatch):
    """Replace the cache-decorated fetch and netfetch.get_client with plain
    httpx so respx intercepts requests without Redis/rate-limiting."""

    async def _fetch(_query):
        async with httpx.AsyncClient() as c:
            r = await c.get(WMN_URL, timeout=30.0)
            r.raise_for_status()
            return r.json()

    async def _fake_get_client(_policy="default"):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(wmn_mod, "_fetch_wmn_data", _fetch)
    monkeypatch.setattr(wmn_mod, "get_client", _fake_get_client)


def test_matches_hit_by_status_and_string():
    site = {"e_code": 200, "e_string": "found", "m_code": 404, "m_string": "missing"}
    assert _matches(site, 200, "the user was found here") is True


def test_matches_rejects_wrong_status():
    site = {"e_code": 200, "e_string": "found"}
    assert _matches(site, 404, "found") is False


def test_matches_rejects_when_e_string_missing():
    site = {"e_code": 200, "e_string": "Repositories"}
    assert _matches(site, 200, "totally different body") is False


def test_matches_rejects_on_m_string():
    site = {"e_code": 200, "e_string": "x", "m_string": "no such user"}
    assert _matches(site, 200, "x but no such user") is False


@pytest.mark.asyncio
async def test_collector_emits_matches_and_dedups():
    user = "alice"
    with respx.mock(assert_all_called=False) as router:
        router.get(WMN_URL).mock(return_value=httpx.Response(200, json=SAMPLE_DATA))
        # GitHub: hit (status 200 + e_string present)
        router.get(f"https://github.com/{user}").mock(
            return_value=httpx.Response(
                200, text="<html>Pinned Repositories list</html>"
            )
        )
        # Reddit: miss (status 404 + m_string)
        router.get(f"https://www.reddit.com/user/{user}").mock(
            return_value=httpx.Response(404, text="Sorry, page not found")
        )
        # FakeSite: hit (status 200 + e_string)
        router.get(f"https://fakesite.example/u/{user}").mock(
            return_value=httpx.Response(200, text="Profile of alice here")
        )

        collector = WhatsMyNameCollector()
        findings = []
        async for f in collector.run(SearchInput(username=user)):
            findings.append(f)

    titles = {f.title for f in findings}
    assert "GitHub" in titles
    assert "FakeSite" in titles
    assert "Reddit" not in titles
    assert len(findings) == 2

    gh = next(f for f in findings if f.title == "GitHub")
    assert gh.collector == "whatsmyname"
    assert gh.category == "account"
    assert gh.entity_type == "account"
    assert gh.confidence == 0.7
    assert gh.payload == {"platform": "GitHub", "category": "coding", "username": user}
    assert gh.url and "github.com/alice" in gh.url

    # Dedup check: fingerprint stable + no duplicate URLs.
    urls = [f.url for f in findings]
    assert len(urls) == len(set(urls))


@pytest.mark.asyncio
async def test_collector_silent_on_request_errors():
    user = "bob"
    with respx.mock(assert_all_called=False) as router:
        router.get(WMN_URL).mock(return_value=httpx.Response(200, json=SAMPLE_DATA))
        router.get(f"https://github.com/{user}").mock(
            side_effect=httpx.ConnectError("boom")
        )
        router.get(f"https://www.reddit.com/user/{user}").mock(
            side_effect=httpx.ReadTimeout("slow")
        )
        router.get(f"https://fakesite.example/u/{user}").mock(
            return_value=httpx.Response(200, text="Profile of bob")
        )

        collector = WhatsMyNameCollector()
        findings = [f async for f in collector.run(SearchInput(username=user))]

    # Only FakeSite survives; the other two errored silently.
    assert [f.title for f in findings] == ["FakeSite"]


@pytest.mark.asyncio
async def test_collector_handles_empty_sites_list():
    with respx.mock(assert_all_called=False) as router:
        router.get(WMN_URL).mock(return_value=httpx.Response(200, json={"sites": []}))
        collector = WhatsMyNameCollector()
        findings = [f async for f in collector.run(SearchInput(username="x"))]
    assert findings == []
