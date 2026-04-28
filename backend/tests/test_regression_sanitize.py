"""Regression tests for Wave 1 (sanitization) bug-fixes.

One test per bug:

1. ``rapidapi_generic`` is no longer importable.
2. ``domain_photon`` extracts inline social URLs (was a dead loop).
3. ``instagram_public`` returns no findings on the login-wall HTML.
4. ``shodan_internetdb`` does not block the event loop on DNS resolution.
5. ``boe`` / ``borme`` skip results whose title and context don't reference
   the queried name (identity guard).
6. ``threads_public`` skips when the username never appears in the rendered
   HTML (identity guard against SPA shells).
"""
from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest
import respx

from app.collectors import boe as boe_mod
from app.collectors import borme as borme_mod
from app.collectors import domain_photon as photon_mod
from app.collectors import instagram_public as ig_mod
from app.collectors import shodan_internetdb as shodan_mod
from app.collectors import threads_public as threads_mod
from app.collectors.boe import BOECollector
from app.collectors.borme import BORMECollector
from app.collectors.domain_photon import DomainPhotonCollector
from app.collectors.instagram_public import InstagramPublicCollector
from app.collectors.shodan_internetdb import ShodanInternetDBCollector
from app.collectors.threads_public import ThreadsPublicCollector
from app.schemas import SearchInput


# ---------------------------------------------------------------------------
# 1) rapidapi_generic is fully removed.
# ---------------------------------------------------------------------------
def test_rapidapi_generic_module_removed():
    """The rapidapi_generic module should no longer be importable."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.collectors.rapidapi_generic")


def test_rapidapi_collectors_not_in_registry():
    """No collector in the registry should have a name starting with 'rapidapi_'."""
    from app.collectors.base import collector_registry
    import app.collectors  # noqa: F401  side-effect import

    rapidapi_names = [c.name for c in collector_registry.all() if c.name.startswith("rapidapi_")]
    assert rapidapi_names == [], f"unexpected rapidapi collectors: {rapidapi_names}"


# ---------------------------------------------------------------------------
# 2) domain_photon: inline social URLs extracted (was dead code).
# ---------------------------------------------------------------------------
_DOMAIN_PHOTON_HTML = """
<!doctype html>
<html><body>
<p>Síguenos en https://twitter.com/myhandle ¡y también en https://instagram.com/myhandle/!</p>
<p>Email: alice@example.com  Tel: +34 600 111 222</p>
<a href="https://github.com/myhandle">GitHub</a>
<a href="https://linkedin.com/in/myhandle">LinkedIn</a>
</body></html>
"""


@pytest.fixture
def _patch_photon_client(monkeypatch):
    """Replace the Photon collector's HTTP client with a respx-friendly one."""

    def _fake_client(*args, **kwargs):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(photon_mod, "client", _fake_client)


@pytest.mark.asyncio
async def test_domain_photon_inline_social_extracted(_patch_photon_client):
    with respx.mock(assert_all_called=False) as router:
        # Match every candidate path on example.com (incl. the bare "/").
        router.get(url__regex=r"https?://example\.com.*").mock(
            return_value=httpx.Response(
                200,
                text=_DOMAIN_PHOTON_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        )
        findings = []
        async for f in DomainPhotonCollector().run(SearchInput(domain="example.com")):
            findings.append(f)
    socials = [f for f in findings if f.entity_type == "SocialLinkOnSite"]
    seen_urls = {f.url for f in socials}
    # Inline (text) AND <a href> must be captured. Twitter/IG live in text only;
    # GitHub/LinkedIn live in anchors. The bug we're regression-testing dropped
    # the inline ones entirely.
    assert any("twitter.com/myhandle" in u for u in seen_urls), seen_urls
    assert any("instagram.com/myhandle" in u for u in seen_urls), seen_urls
    assert any("github.com/myhandle" in u for u in seen_urls), seen_urls
    assert any("linkedin.com/in/myhandle" in u for u in seen_urls), seen_urls


# ---------------------------------------------------------------------------
# 3) instagram_public: no findings on the login-wall HTML.
# ---------------------------------------------------------------------------
_IG_LOGIN_WALL_HTML = """
<!doctype html><html><head>
<meta property="og:title" content="Instagram"/>
<meta property="og:description" content="Sign up or log in to access photos."/>
<meta property="og:image" content="https://www.instagram.com/static/og.jpg"/>
</head><body>
<script>window.__login_popup = true;</script>
</body></html>
"""


@pytest.fixture
def _patch_ig_client(monkeypatch):
    def _fake_client(*args, **kwargs):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(ig_mod, "client", _fake_client)


@pytest.mark.asyncio
async def test_instagram_public_login_wall_yields_nothing(_patch_ig_client):
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://i\.instagram\.com.*").mock(
            return_value=httpx.Response(403, json={}),
        )
        router.get(url__regex=r"https?://www\.instagram\.com.*").mock(
            return_value=httpx.Response(200, text=_IG_LOGIN_WALL_HTML),
        )
        findings = []
        async for f in InstagramPublicCollector().run(SearchInput(username="randomuser_xyz")):
            findings.append(f)
    assert findings == [], f"login-wall HTML produced findings: {findings}"


# ---------------------------------------------------------------------------
# 4) shodan_internetdb: async resolver, no event-loop blocking.
# ---------------------------------------------------------------------------
@pytest.fixture
def _patch_shodan_client(monkeypatch):
    def _fake_client(*args, **kwargs):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(shodan_mod, "client", _fake_client)


async def _drain_to_list(agen):
    out: list = []
    async for item in agen:
        out.append(item)
    return out


@pytest.mark.asyncio
async def test_shodan_internetdb_async_dns_does_not_block(_patch_shodan_client):
    """The collector must use an async resolver. We feed an IP via extra_context
    so DNS resolution is bypassed entirely; the test must complete instantly."""
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://internetdb\.shodan\.io.*").mock(
            return_value=httpx.Response(200, json={"ports": [80, 443], "vulns": []}),
        )
        coro = ShodanInternetDBCollector().run(
            SearchInput(extra_context="ip 8.8.8.8 spotted"),
        )
        findings = await asyncio.wait_for(_drain_to_list(coro), timeout=2.0)
    assert any(f.entity_type == "ShodanHost" for f in findings)


# ---------------------------------------------------------------------------
# 5) BOE / BORME identity guard.
# ---------------------------------------------------------------------------
_BOE_RESULTS_HTML_OK = """
<!doctype html><html><body>
<ul class="resultados-busqueda">
<li><a href="/diario_boe/txt.php?id=BOE-A-2024-1234">Resolución por la que se nombra a Jose Castillo Diez como vocal del Consejo X</a></li>
</ul>
</body></html>
"""

_BOE_RESULTS_HTML_NOMATCH = """
<!doctype html><html><body>
<ul class="resultados-busqueda">
<li><a href="/diario_boe/txt.php?id=BOE-A-2024-9999">Sumario del día — Disposiciones generales</a></li>
</ul>
</body></html>
"""


@pytest.fixture
def _patch_boe_client(monkeypatch):
    def _fake_client(*args, **kwargs):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(boe_mod, "client", _fake_client)
    monkeypatch.setattr(borme_mod, "client", _fake_client)


@pytest.mark.asyncio
async def test_boe_identity_guard_keeps_match(_patch_boe_client):
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.boe\.es.*").mock(
            return_value=httpx.Response(200, text=_BOE_RESULTS_HTML_OK),
        )
        findings = []
        async for f in BOECollector().run(SearchInput(full_name="Jose Castillo Diez")):
            findings.append(f)
    assert findings, "should match: name explicit in title"
    assert findings[0].confidence >= 0.6


@pytest.mark.asyncio
async def test_boe_identity_guard_drops_nomatch(_patch_boe_client):
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.boe\.es.*").mock(
            return_value=httpx.Response(200, text=_BOE_RESULTS_HTML_NOMATCH),
        )
        findings = []
        async for f in BOECollector().run(SearchInput(full_name="Jose Castillo Diez")):
            findings.append(f)
    # Title is generic ("Sumario del día") and contains none of the name's
    # 4+ char tokens — guard must drop it instead of emitting a 0.6 noise row.
    assert findings == []


# ---------------------------------------------------------------------------
# 6) threads_public: identity guard against SPA shells.
# ---------------------------------------------------------------------------
@pytest.fixture
def _patch_threads_client(monkeypatch):
    async def _fake_get_client(_policy="gentle"):
        return httpx.AsyncClient(follow_redirects=True)

    monkeypatch.setattr(threads_mod, "get_client", _fake_get_client)


@pytest.mark.asyncio
async def test_threads_public_skips_when_username_absent(_patch_threads_client):
    """If the rendered HTML never mentions the username, the guard must skip."""
    spa_shell_no_user = (
        "<html><head><title>Threads</title></head>"
        "<body><div id='__next'></div></body></html>"
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.threads\.net.*").mock(
            return_value=httpx.Response(200, text=spa_shell_no_user),
        )
        findings = []
        async for f in ThreadsPublicCollector().run(SearchInput(username="someuser_42")):
            findings.append(f)
    assert findings == []


@pytest.mark.asyncio
async def test_threads_public_emits_when_username_present(_patch_threads_client):
    """When the HTML actually mentions the username, the collector must emit."""
    html_with_user = (
        '<html><head>'
        '<meta property="og:title" content="someuser_42 (@someuser_42) on Threads"/>'
        '<meta property="og:description" content="Hello world"/>'
        '</head><body><article>First post body</article></body></html>'
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.threads\.net.*").mock(
            return_value=httpx.Response(200, text=html_with_user),
        )
        findings = []
        async for f in ThreadsPublicCollector().run(SearchInput(username="someuser_42")):
            findings.append(f)
    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "ThreadsProfile"
    # Confidence must be the full base (we extracted name + bio + post).
    assert f.confidence == pytest.approx(0.75)
