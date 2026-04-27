"""Tests for the LinkedIn public collector.

Uses a hand-rolled VCR-style cassette stub: we monkey-patch ``httpx.AsyncClient``
to serve canned responses keyed by URL pattern. This avoids the cost of a
real LinkedIn round-trip and keeps the test deterministic.

Record/replay strategy is intentionally light here: the collector is not yet
wired into the registry (see WIRING note in collectors/linkedin_public.py),
so we test it directly against a fake transport.
"""
from __future__ import annotations

import httpx
import pytest

from app.collectors.linkedin_public import LinkedInPublicCollector
from app.schemas import SearchInput


# ---------------------------------------------------------------------------
# Cassette stubs
# ---------------------------------------------------------------------------

_PROFILE_HTML_OK = """<!doctype html>
<html><head>
  <meta property="og:title" content="Jane Doe - Senior Engineer at ACME" />
  <meta property="og:description"
        content="Senior Engineer at ACME · Educación: MIT · Ubicación: Madrid · 500+ contactos" />
  <meta property="og:image" content="https://media.licdn.com/jane.jpg" />
  <link rel="canonical" href="https://www.linkedin.com/in/jane-doe" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "ProfilePage",
        "mainEntity": {"@id": "#person"}
      },
      {
        "@type": "Person",
        "@id": "#person",
        "name": "Jane Doe",
        "jobTitle": "Senior Engineer",
        "image": {"@type": "ImageObject", "contentUrl": "https://media.licdn.com/jane.jpg"},
        "address": {
          "@type": "PostalAddress",
          "addressLocality": "Madrid",
          "addressCountry": "ES"
        },
        "worksFor": [
          {"@type": "Organization", "name": "ACME", "url": "https://acme.example"}
        ],
        "alumniOf": [
          {"@type": "EducationalOrganization", "name": "MIT", "url": "https://mit.edu"}
        ]
      }
    ]
  }
  </script>
</head><body>profile</body></html>
"""


_SEARXNG_OK = {
    "results": [
        {"url": "https://www.linkedin.com/in/jane-doe", "title": "Jane Doe", "engine": "duckduckgo"},
        {"url": "https://example.com/other", "title": "noise"},
    ]
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Minimal async-context-manager replacement for httpx.AsyncClient."""

    def __init__(self, routes):
        self._routes = routes  # list[(predicate, response)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        for predicate, response in self._routes:
            if predicate(url, kwargs):
                return response
        return _FakeResponse(404, text="not stubbed")


def _install_fake(monkeypatch, routes):
    def _factory(*args, **kwargs):
        return _FakeClient(routes)

    monkeypatch.setattr("app.collectors.linkedin_public.client", _factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linkedin_username_direct(monkeypatch):
    routes = [
        (
            lambda u, k: "linkedin.com/in/jane-doe" in u,
            _FakeResponse(200, text=_PROFILE_HTML_OK),
        ),
    ]
    _install_fake(monkeypatch, routes)

    findings = []
    async for f in LinkedInPublicCollector().run(SearchInput(username="jane-doe")):
        findings.append(f)

    assert len(findings) == 1
    f = findings[0]
    assert f.collector == "linkedin_public"
    assert f.entity_type == "LinkedInPublicProfile"
    assert f.url == "https://www.linkedin.com/in/jane-doe"
    p = f.payload
    assert p["headline"] == "Senior Engineer"
    assert p["current_company"] == "ACME"
    assert p["location"].startswith("Madrid")
    assert p["photo_url"] == "https://media.licdn.com/jane.jpg"
    assert any(e["school"] == "MIT" for e in p["education"])
    assert any(e["company"] == "ACME" for e in p["experience"])


@pytest.mark.asyncio
async def test_linkedin_via_searxng_when_no_username(monkeypatch):
    routes = [
        (
            lambda u, k: "/search" in u and "linkedin.com/in/" in (k.get("params") or {}).get("q", ""),
            _FakeResponse(200, json_data=_SEARXNG_OK),
        ),
        (
            lambda u, k: "linkedin.com/in/jane-doe" in u,
            _FakeResponse(200, text=_PROFILE_HTML_OK),
        ),
    ]
    _install_fake(monkeypatch, routes)

    findings = []
    async for f in LinkedInPublicCollector().run(SearchInput(full_name="Jane Doe")):
        findings.append(f)

    assert len(findings) == 1
    assert "jane-doe" in (findings[0].url or "")
    # Lower confidence since slug came from a dork, not direct input.
    assert findings[0].confidence < 0.85


@pytest.mark.asyncio
async def test_linkedin_blocked_returns_empty(monkeypatch):
    routes = [
        (lambda u, k: "linkedin.com/in/" in u, _FakeResponse(999, text="blocked")),
    ]
    _install_fake(monkeypatch, routes)

    findings = []
    async for f in LinkedInPublicCollector().run(SearchInput(username="anything")):
        findings.append(f)
    assert findings == []


@pytest.mark.asyncio
async def test_linkedin_429_returns_empty(monkeypatch):
    routes = [
        (lambda u, k: "linkedin.com/in/" in u, _FakeResponse(429, text="rate-limited")),
    ]
    _install_fake(monkeypatch, routes)

    findings = []
    async for f in LinkedInPublicCollector().run(SearchInput(username="anything")):
        findings.append(f)
    assert findings == []


@pytest.mark.asyncio
async def test_linkedin_no_inputs_no_calls(monkeypatch):
    # No username, no name, no email -> early return without HTTP.
    called = {"n": 0}

    class _Guard(_FakeClient):
        async def get(self, url, **kwargs):  # pragma: no cover - should not run
            called["n"] += 1
            return _FakeResponse(200, text="")

    def _factory(*a, **k):
        return _Guard(routes=[])

    monkeypatch.setattr("app.collectors.linkedin_public.client", _factory)

    findings = []
    async for f in LinkedInPublicCollector().run(SearchInput(domain="example.com")):
        findings.append(f)
    assert findings == []
    # The collector still opens the client context, but should issue zero GETs.
    assert called["n"] == 0


def test_linkedin_applicable_signals():
    c = LinkedInPublicCollector()
    assert c.applicable(SearchInput(username="jane-doe"))
    assert c.applicable(SearchInput(full_name="Jane Doe"))
    assert c.applicable(SearchInput(email="jane@example.com"))
    assert not c.applicable(SearchInput(domain="example.com"))
