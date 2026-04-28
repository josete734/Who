"""Tests for Wave 8 — new collectors that ship without external API keys.

One happy-path test per collector + a "no input" guard test where it
makes sense. We use ``respx`` with regex URL matchers so the collectors'
exact path-building doesn't have to be mirrored verbatim in fixtures.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.collectors import (
    huggingface as hf_mod,
    infosubvenciones as bdns_mod,
    passive_dns as pdns_mod,
    rdap as rdap_mod,
    security_headers as sec_mod,
    transparencia_es as transp_mod,
    youtube as yt_mod,
)
from app.collectors.huggingface import HuggingFaceProfileCollector
from app.collectors.infosubvenciones import InfoSubvencionesCollector
from app.collectors.passive_dns import PassiveDNSCollector
from app.collectors.rdap import RDAPDomainCollector
from app.collectors.security_headers import SecurityHeadersCollector
from app.collectors.transparencia_es import TransparenciaESCollector
from app.collectors.youtube import YouTubeChannelCollector
from app.schemas import SearchInput


@pytest.fixture(autouse=True)
def _patch_clients(monkeypatch):
    """Replace the per-module ``client`` with a respx-friendly httpx one."""

    def _fake(*a, **kw):
        return httpx.AsyncClient(follow_redirects=True)

    for mod in (yt_mod, rdap_mod, pdns_mod, sec_mod, hf_mod, transp_mod, bdns_mod):
        monkeypatch.setattr(mod, "client", _fake, raising=True)


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------
_YT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry><yt:videoId>aaa</yt:videoId><title>First video</title><published>2024-01-01T00:00:00+00:00</published></entry>
  <entry><yt:videoId>bbb</yt:videoId><title>Second video</title><published>2024-02-01T00:00:00+00:00</published></entry>
</feed>"""


@pytest.mark.asyncio
async def test_youtube_collector_emits_channel_and_videos():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.youtube\.com/oembed.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "author_name": "Test Channel",
                    "thumbnail_url": "https://i.ytimg.com/test.jpg",
                },
            )
        )
        router.get(url__regex=r"https?://www\.youtube\.com/feeds/videos\.xml.*").mock(
            return_value=httpx.Response(200, text=_YT_FEED)
        )
        findings = []
        async for f in YouTubeChannelCollector().run(SearchInput(username="testchannel")):
            findings.append(f)
    types = {f.entity_type for f in findings}
    assert "YouTubeChannel" in types
    assert "YouTubeRecentVideos" in types
    videos = next(f for f in findings if f.entity_type == "YouTubeRecentVideos").payload["videos"]
    assert len(videos) == 2
    assert {v["video_id"] for v in videos} == {"aaa", "bbb"}


@pytest.mark.asyncio
async def test_youtube_collector_no_username_returns_empty():
    findings = [f async for f in YouTubeChannelCollector().run(SearchInput())]
    assert findings == []


# ---------------------------------------------------------------------------
# RDAP
# ---------------------------------------------------------------------------
_RDAP_PAYLOAD = {
    "objectClassName": "domain",
    "ldhName": "example.com",
    "entities": [
        {
            "roles": ["registrar"],
            "vcardArray": ["vcard", [["fn", {}, "text", "ExampleRegistrar Inc."]]],
        },
        {
            "roles": ["registrant"],
            "vcardArray": ["vcard", [
                ["fn", {}, "text", "Jane Owner"],
                ["email", {}, "text", "owner@example.com"],
            ]],
        },
    ],
    "events": [
        {"eventAction": "registration", "eventDate": "2010-01-01T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
    ],
    "nameservers": [
        {"ldhName": "NS1.EXAMPLE.COM"},
        {"ldhName": "NS2.EXAMPLE.COM"},
    ],
    "status": ["clientTransferProhibited"],
}


@pytest.mark.asyncio
async def test_rdap_collector_extracts_registrar_and_dates():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://rdap\.org/domain/.*").mock(
            return_value=httpx.Response(200, json=_RDAP_PAYLOAD)
        )
        findings = [f async for f in RDAPDomainCollector().run(SearchInput(domain="example.com"))]
    assert len(findings) == 1
    p = findings[0].payload
    assert p["registrar"] == "ExampleRegistrar Inc."
    assert p["registrant_name"] == "Jane Owner"
    assert p["registrant_email"] == "owner@example.com"
    assert p["registered"].startswith("2010")
    assert p["expires"].startswith("2030")
    assert sorted(p["nameservers"]) == ["ns1.example.com", "ns2.example.com"]
    assert "clientTransferProhibited" in p["statuses"]


@pytest.mark.asyncio
async def test_rdap_collector_strips_protocol_from_input():
    """Domain with leading https:// should still resolve."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r"https?://rdap\.org/domain/.*").mock(
            return_value=httpx.Response(200, json=_RDAP_PAYLOAD)
        )
        async for _ in RDAPDomainCollector().run(SearchInput(domain="https://example.com/foo")):
            pass
    assert any("example.com" in str(c.request.url) for c in route.calls)


# ---------------------------------------------------------------------------
# Passive DNS
# ---------------------------------------------------------------------------
_HACKERTARGET_BODY = (
    "api.example.com,93.184.216.34\n"
    "blog.example.com,93.184.216.40\n"
    "www.example.com,93.184.216.34\n"  # duplicate IP
)
_THREATMINER_BODY = {
    "results": [
        {"ip": "192.0.2.10", "last_seen": "2023-08-15"},
        {"ip": "93.184.216.34", "last_seen": "2024-01-01"},  # already seen via HT
        {"ip": "192.0.2.20", "last_seen": "2023-12-01"},
    ]
}


@pytest.mark.asyncio
async def test_passive_dns_collector_aggregates_two_sources():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://api\.hackertarget\.com/.*").mock(
            return_value=httpx.Response(200, text=_HACKERTARGET_BODY)
        )
        router.get(url__regex=r"https?://api\.threatminer\.org/.*").mock(
            return_value=httpx.Response(200, json=_THREATMINER_BODY)
        )
        findings = [f async for f in PassiveDNSCollector().run(SearchInput(domain="example.com"))]
    subs = {f.payload["subdomain"] for f in findings if f.entity_type == "Subdomain"}
    ips = {f.payload["ip"] for f in findings if f.entity_type == "HistoricalIP"}
    assert {"api.example.com", "blog.example.com", "www.example.com"} <= subs
    # ThreatMiner adds two distinct historical IPs (not in HT subdomain rows).
    assert "192.0.2.10" in ips
    assert "192.0.2.20" in ips


@pytest.mark.asyncio
async def test_passive_dns_skips_api_quota_message():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://api\.hackertarget\.com/.*").mock(
            return_value=httpx.Response(200, text="API count exceeded - Free tier")
        )
        router.get(url__regex=r"https?://api\.threatminer\.org/.*").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        findings = [f async for f in PassiveDNSCollector().run(SearchInput(domain="example.com"))]
    # No subdomains because HT was over quota; no IPs because TM was empty.
    assert findings == []


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_security_headers_collector_extracts_fingerprint():
    headers = {
        "Server": "nginx/1.27",
        "X-Powered-By": "Express",
        "CF-Ray": "abc123-MAD",
        "Strict-Transport-Security": "max-age=31536000",
        "Content-Security-Policy": (
            "default-src 'self' https://cdn.example.com; "
            "script-src https://www.googletagmanager.com"
        ),
    }
    with respx.mock(assert_all_called=False) as router:
        router.head(url__regex=r"https?://example\.com/").mock(
            return_value=httpx.Response(200, headers=headers)
        )
        router.get(url__regex=r"https?://example\.com/\.well-known/security\.txt").mock(
            return_value=httpx.Response(
                200,
                text=(
                    "Contact: mailto:security@example.com\n"
                    "Policy: https://example.com/security-policy\n"
                ),
            )
        )
        findings = [f async for f in SecurityHeadersCollector().run(SearchInput(domain="example.com"))]
    fp = next(f for f in findings if f.entity_type == "HTTPFingerprint")
    assert fp.payload["headers"]["Server"] == "nginx/1.27"
    assert "CF-Ray" in fp.payload["headers"]
    third = fp.payload["csp_third_parties"]
    assert "cdn.example.com" in third
    assert "www.googletagmanager.com" in third
    sec = next(f for f in findings if f.entity_type == "SecurityTxt")
    assert "mailto:security@example.com" in sec.payload["contacts"]


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_huggingface_collector_extracts_profile_fields():
    body = {
        "user": {
            "user": "janedoe",
            "fullname": "Jane Doe",
            "avatarUrl": "https://hf.co/avatars/jd.png",
            "isPro": True,
            "githubUser": "janedoe",
            "twitterUser": "janedoe",
            "orgs": [{"name": "AI4Good"}, {"name": "OSSML"}],
        },
        "modelsCount": 42,
        "datasetsCount": 7,
        "spacesCount": 3,
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://huggingface\.co/api/users/.*").mock(
            return_value=httpx.Response(200, json=body)
        )
        findings = [f async for f in HuggingFaceProfileCollector().run(SearchInput(username="janedoe"))]
    assert len(findings) == 1
    p = findings[0].payload
    assert p["full_name"] == "Jane Doe"
    assert p["n_models"] == 42
    assert "AI4Good" in p["orgs"]
    assert p["github"] == "janedoe"


@pytest.mark.asyncio
async def test_huggingface_collector_identity_guard_drops_mismatch():
    """If the API returns a different user (cache poisoning / redirect),
    we must not emit a finding for the requested handle."""
    body = {"user": {"user": "someone_else"}}
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://huggingface\.co/api/users/.*").mock(
            return_value=httpx.Response(200, json=body)
        )
        findings = [f async for f in HuggingFaceProfileCollector().run(SearchInput(username="janedoe"))]
    assert findings == []


# ---------------------------------------------------------------------------
# InfoSubvenciones (BDNS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bdns_collector_emits_subvencion_with_identity_guard():
    body = {
        "content": [
            {
                "beneficiario": {"nombre": "Juan García López", "nif": "12345678A"},
                "importe": 5000,
                "fechaConcesion": "2023-04-15",
                "organismo": "Ministerio Cultura",
            },
            {
                # Different person with overlapping surname; identity guard
                # accepts it as a candidate at lower confidence.
                "beneficiario": {"nombre": "Pedro Otero", "nif": "11111111A"},
                "importe": 7000,
                "fechaConcesion": "2024-01-10",
                "organismo": "X",
            },
        ]
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://www\.infosubvenciones\.es/.*").mock(
            return_value=httpx.Response(200, json=body)
        )
        findings = [
            f async for f in InfoSubvencionesCollector().run(
                SearchInput(full_name="Juan García López")
            )
        ]
    # The Juan García record matches by full string → high confidence.
    juans = [f for f in findings if "Juan García" in f.title]
    assert juans, "exact match must surface"
    assert juans[0].confidence >= 0.8
    # The unrelated "Pedro Otero" entry must NOT match the identity guard
    # (no token of length ≥4 from the queried name appears in the title).
    assert all("Pedro Otero" not in f.title for f in findings)


# ---------------------------------------------------------------------------
# Transparencia
# ---------------------------------------------------------------------------
_TRANSP_HTML = """
<html><body>
<a href="/servicios-buscador/contenido/cargo-publico/SubvencionA12345.html">
  Subvención concedida a Juan García López — Ministerio de Cultura
</a>
<a href="/servicios-buscador/contenido/declaracion/Declaracion-XYZ.html">
  Declaración de bienes — Pedro Pérez 2023
</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_transparencia_collector_filters_by_identity():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https?://transparencia\.gob\.es/.*").mock(
            return_value=httpx.Response(200, text=_TRANSP_HTML)
        )
        findings = [
            f async for f in TransparenciaESCollector().run(
                SearchInput(full_name="Juan García López")
            )
        ]
    titles = [f.title for f in findings]
    assert any("Juan García" in t for t in titles)
    # Pedro Pérez doesn't share a 4+ char token with "Juan García López"
    # so the guard drops it.
    assert all("Pedro Pérez" not in t for t in titles)
