"""Tests for Wave 4 (A4.1 + A4.2) collectors.

Covers wigle, foursquare_public, tgstat, combot, disboard. The
subprocess-driven subdomain_passive collector is covered by a separate
unit test that does not hit the network.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.schemas import SearchInput


# ---- helpers --------------------------------------------------------------


@pytest.fixture
def patch_get_client(monkeypatch):
    """Replace ``get_client`` in a target collector module with plain httpx
    so respx intercepts requests (the real client wraps a custom transport
    which respx cannot route through)."""

    def _apply(module):
        async def _fake(_policy="default"):
            return httpx.AsyncClient(follow_redirects=True)

        monkeypatch.setattr(module, "get_client", _fake)
        return module

    return _apply


# ---- wigle ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_wigle_no_credentials_returns_empty(monkeypatch, patch_get_client):
    from app.collectors import wigle as mod

    patch_get_client(mod)

    class _S:
        wigle_basic = ""

    monkeypatch.setattr(mod, "get_settings", lambda: _S())

    findings = [
        f
        async for f in mod.WigleCollector().run(
            SearchInput(extra_context="wigle_ssid=MyHomeSSID")
        )
    ]
    assert findings == []


@pytest.mark.asyncio
async def test_wigle_emits_locations(monkeypatch, patch_get_client):
    from app.collectors import wigle as mod

    patch_get_client(mod)

    class _S:
        wigle_basic = "dXNlcjp0b2tlbg=="

    monkeypatch.setattr(mod, "get_settings", lambda: _S())

    payload = {
        "results": [
            {
                "ssid": "MyHome",
                "netid": "AA:BB:CC:DD:EE:FF",
                "trilat": 40.41,
                "trilong": -3.70,
                "country": "ES",
                "region": "MD",
                "city": "Madrid",
                "lastupdt": "2024-01-01",
                "encryption": "wpa2",
            }
        ]
    }

    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=mod.WIGLE_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )
        findings = [
            f
            async for f in mod.WigleCollector().run(
                SearchInput(extra_context="wigle_ssid=MyHome")
            )
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "location"
    assert f.category == "geo"
    assert f.payload["lat"] == 40.41
    assert f.payload["lon"] == -3.70
    assert f.payload["city"] == "Madrid"


# ---- foursquare_public ---------------------------------------------------


_FSQ_HTML = """
<html><head>
<meta property="og:title" content="Jane Doe (@jane)" />
<meta property="og:description" content="Foodie based in Madrid" />
<script type="application/ld+json">
{"@type":"Restaurant","name":"Bar Pepe","address":{"@type":"PostalAddress","streetAddress":"Calle X 1","addressLocality":"Madrid","addressCountry":"ES"},"geo":{"@type":"GeoCoordinates","latitude":40.42,"longitude":-3.71},"url":"https://foursquare.com/v/bar-pepe/abc12345"}
</script>
</head><body>
<a href="/v/bar-pepe/abc12345">Bar Pepe</a>
<a href="/v/cafeteria-x/def67890">Cafeteria X</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_foursquare_public_emits_checkins(patch_get_client):
    from app.collectors import foursquare_public as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(mod.PROFILE_URL.format(username="jane")).mock(
            return_value=httpx.Response(200, text=_FSQ_HTML)
        )
        findings = [
            f async for f in mod.FoursquarePublicCollector().run(SearchInput(username="jane"))
        ]

    types = {f.entity_type for f in findings}
    assert "account" in types
    assert "checkin" in types
    checkins = [f for f in findings if f.entity_type == "checkin"]
    venues = {c.payload.get("venue") for c in checkins}
    assert "Bar Pepe" in venues


@pytest.mark.asyncio
async def test_foursquare_public_404_silent(patch_get_client):
    from app.collectors import foursquare_public as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(mod.PROFILE_URL.format(username="ghost")).mock(
            return_value=httpx.Response(404, text="")
        )
        findings = [
            f async for f in mod.FoursquarePublicCollector().run(SearchInput(username="ghost"))
        ]
    assert findings == []


# ---- tgstat --------------------------------------------------------------


_TGSTAT_HTML = """
<html><head>
<meta property="og:title" content="My OSINT Channel" />
<meta property="og:description" content="OSINT news and tools" />
</head><body>
<div class="channel-info"><h2>English</h2></div>
<div>12,345 subscribers</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_tgstat_parses_channel(patch_get_client):
    from app.collectors import tgstat as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(mod.PROFILE_URL.format(channel="osintchannel")).mock(
            return_value=httpx.Response(200, text=_TGSTAT_HTML)
        )
        findings = [
            f async for f in mod.TGStatCollector().run(SearchInput(username="osintchannel"))
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "channel"
    assert f.category == "messengers"
    assert f.payload["title"] == "My OSINT Channel"
    assert f.payload["subscribers"] == 12345


# ---- combot --------------------------------------------------------------


_COMBOT_HTML = """
<html><head>
<meta property="og:title" content="Some Group" />
<meta property="og:description" content="Discussion group" />
</head><body>
<p>5,432 members</p>
<p>120 messages per day</p>
</body></html>
"""


@pytest.mark.asyncio
async def test_combot_parses_group(patch_get_client):
    from app.collectors import combot as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(mod.PROFILE_URL.format(channel="somegroup")).mock(
            return_value=httpx.Response(200, text=_COMBOT_HTML)
        )
        findings = [
            f async for f in mod.CombotCollector().run(SearchInput(username="somegroup"))
        ]

    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "channel"
    assert f.payload["members"] == 5432
    assert f.payload["messages_per_day"] == 120


# ---- disboard ------------------------------------------------------------


_DISBOARD_HTML = """
<html><body>
<div class="server">
  <a href="/servers/123456789012345678">Cool Server</a>
  <span>4,321 members</span>
</div>
<div class="server">
  <a href="/servers/987654321098765432">Other Server</a>
  <span>123 online</span>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_disboard_emits_servers(patch_get_client):
    from app.collectors import disboard as mod

    patch_get_client(mod)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=mod.SEARCH_URL).mock(
            return_value=httpx.Response(200, text=_DISBOARD_HTML)
        )
        findings = [
            f async for f in mod.DisboardCollector().run(SearchInput(username="cool"))
        ]

    assert len(findings) >= 2
    for f in findings:
        assert f.entity_type == "discord_server"
        assert f.category == "messengers"
    ids = {f.payload["server_id"] for f in findings}
    assert "123456789012345678" in ids


# ---- subdomain_passive (offline) -----------------------------------------


@pytest.mark.asyncio
async def test_subdomain_passive_no_binaries(monkeypatch):
    from app.collectors import subdomain_passive as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    findings = [
        f async for f in mod.SubdomainPassiveCollector().run(SearchInput(domain="example.com"))
    ]
    assert findings == []


@pytest.mark.asyncio
async def test_subdomain_passive_parses_subfinder_output(monkeypatch):
    from app.collectors import subdomain_passive as mod

    monkeypatch.setattr(
        mod.shutil, "which", lambda name: "/usr/bin/" + name if name == "subfinder" else None
    )

    async def _fake_run(cmd, timeout):
        return ["api.example.com", "www.example.com", "example.com", "junk"]

    monkeypatch.setattr(mod, "_run", _fake_run)

    findings = [
        f async for f in mod.SubdomainPassiveCollector().run(SearchInput(domain="example.com"))
    ]
    subs = {f.payload["subdomain"] for f in findings}
    assert subs == {"api.example.com", "www.example.com"}
    for f in findings:
        assert f.entity_type == "subdomain"
        assert f.category == "domain"
