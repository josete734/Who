"""Tests for the Twitter/X Nitter collector with mocked instances."""
from __future__ import annotations

import importlib

import httpx
import pytest

from app.schemas import SearchInput


PROFILE_HTML = """
<html><body>
<a class="profile-card-fullname" href="/jane">Jane Doe</a>
<div class="profile-bio"><p>OSINT enthusiast. Visit https://example.com/jane and https://blog.example.org</p></div>
<div class="profile-location"><span class="icon-location"></span><span>Madrid, ES</span></div>
<div class="profile-joindate"><span title="6:12 PM - 4 May 2015">Joined May 2015</span></div>
<div class="profile-website"><a href="https://example.com/jane">example.com/jane</a></div>
<ul class="profile-statlist">
  <li class="posts"><span class="profile-stat-num">1,234</span></li>
  <li class="following"><span class="profile-stat-num">321</span></li>
  <li class="followers"><span class="profile-stat-num">9,876</span></li>
</ul>
</body></html>
"""

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Jane / @jane</title>
<item>
  <title>Hello world from Jane</title>
  <link>https://nitter.example/jane/status/111</link>
  <pubDate>Mon, 01 Apr 2024 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Second post &amp; more</title>
  <link>https://nitter.example/jane/status/222</link>
  <pubDate>Sun, 31 Mar 2024 09:30:00 GMT</pubDate>
</item>
</channel></rss>
"""

NOT_FOUND_HTML = '<html><body><div class="error-panel">User not found</div></body></html>'


def _make_handler(routes: dict[tuple[str, str], httpx.Response]):
    """Build an httpx MockTransport handler from a (host, path)->Response map."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.url.host, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, text="not mocked")

    return handler


@pytest.fixture
def patch_client(monkeypatch):
    """Patch app.collectors.twitter_nitter.client to use an httpx MockTransport."""

    def _apply(routes):
        from app.collectors import twitter_nitter as mod

        transport = httpx.MockTransport(_make_handler(routes))

        def fake_client(timeout: float = 15.0, **extra):
            return httpx.AsyncClient(transport=transport, timeout=timeout)

        monkeypatch.setattr(mod, "client", fake_client)
        return mod

    return _apply


@pytest.fixture(autouse=True)
def _set_instances(monkeypatch):
    monkeypatch.setenv(
        "NITTER_INSTANCES",
        "nitter.a.test,nitter.b.test,nitter.c.test",
    )
    # Re-import to ensure no cached default list interferes.
    import app.collectors.twitter_nitter as mod
    importlib.reload(mod)
    yield


async def _collect(mod, username: str):
    inp = SearchInput(username=username)
    out = []
    async for f in mod.TwitterNitterCollector().run(inp):
        out.append(f)
    return out


async def test_first_instance_success(patch_client):
    routes = {
        ("nitter.a.test", "/jane"): httpx.Response(200, text=PROFILE_HTML),
        ("nitter.a.test", "/jane/rss"): httpx.Response(
            200, content=RSS_XML, headers={"Content-Type": "application/rss+xml"}
        ),
    }
    mod = patch_client(routes)
    findings = await _collect(mod, "jane")

    assert findings, "expected at least one finding"
    profile = findings[0]
    assert profile.entity_type == "TwitterProfile"
    assert profile.url == "https://twitter.com/jane"
    pm = profile.payload["profile_meta"]
    assert pm["full_name"] == "Jane Doe"
    assert pm["location"] == "Madrid, ES"
    assert pm["followers"] == 9876
    assert pm["following"] == 321
    assert pm["tweets_count"] == 1234
    assert "https://example.com/jane" in pm["external_links"]
    assert "https://blog.example.org" in pm["external_links"]
    assert pm["source_instance"] == "nitter.a.test"

    tweets = profile.payload["tweets"]
    assert len(tweets) == 2
    assert tweets[0]["url"].startswith("https://twitter.com/jane/status/")
    assert tweets[0]["text"].startswith("Hello world")

    bio_links = [f for f in findings if f.entity_type == "ExternalLink"]
    assert {f.url for f in bio_links} >= {
        "https://example.com/jane",
        "https://blog.example.org",
    }


async def test_round_robin_skips_5xx(patch_client):
    routes = {
        ("nitter.a.test", "/jane"): httpx.Response(503, text="busy"),
        ("nitter.b.test", "/jane"): httpx.Response(429, text="slow down"),
        ("nitter.c.test", "/jane"): httpx.Response(200, text=PROFILE_HTML),
        ("nitter.c.test", "/jane/rss"): httpx.Response(200, content=RSS_XML),
    }
    mod = patch_client(routes)
    findings = await _collect(mod, "jane")
    assert findings
    assert findings[0].payload["profile_meta"]["source_instance"] == "nitter.c.test"


async def test_user_not_found_short_circuits(patch_client):
    routes = {
        ("nitter.a.test", "/ghost"): httpx.Response(200, text=NOT_FOUND_HTML),
        # Even if other instances would succeed, we should not hit them.
        ("nitter.b.test", "/ghost"): httpx.Response(200, text=PROFILE_HTML),
    }
    mod = patch_client(routes)
    findings = await _collect(mod, "ghost")
    assert findings == []


async def test_rss_failure_falls_back_to_other_mirror(patch_client):
    routes = {
        ("nitter.a.test", "/jane"): httpx.Response(200, text=PROFILE_HTML),
        ("nitter.a.test", "/jane/rss"): httpx.Response(429, text="ratelimit"),
        ("nitter.b.test", "/jane/rss"): httpx.Response(200, content=RSS_XML),
    }
    mod = patch_client(routes)
    findings = await _collect(mod, "jane")
    assert findings
    tweets = findings[0].payload["tweets"]
    assert len(tweets) == 2
    assert findings[0].payload["profile_meta"]["source_instance"] == "nitter.a.test"


async def test_all_instances_fail_yields_nothing(patch_client):
    routes = {
        ("nitter.a.test", "/jane"): httpx.Response(502),
        ("nitter.b.test", "/jane"): httpx.Response(503),
        ("nitter.c.test", "/jane"): httpx.Response(500),
    }
    mod = patch_client(routes)
    findings = await _collect(mod, "jane")
    assert findings == []


def test_instances_from_env_default(monkeypatch):
    monkeypatch.delenv("NITTER_INSTANCES", raising=False)
    import app.collectors.twitter_nitter as mod
    importlib.reload(mod)
    assert mod._instances_from_env() == list(mod.DEFAULT_INSTANCES)


def test_collector_not_auto_registered():
    from app.collectors.base import collector_registry

    assert collector_registry.by_name("twitter_nitter") is None
