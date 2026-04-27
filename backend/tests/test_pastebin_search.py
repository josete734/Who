"""Unit tests for the Pastebin / IDE-paste search collector.

The SearXNG endpoint and paste raw-body fetches are mocked via
``respx`` so the test runs offline.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.pastebin_search import (
    PASTE_SITES,
    PastebinSearchCollector,
    _find_match,
    _redact,
)
from app.schemas import SearchInput


def test_redact_removes_term_case_insensitive():
    assert _redact("contact ALICE@example.com today", "alice@example.com") == "contact [REDACTED] today"


def test_find_match_flags_sensitive_context():
    body = "user: alice@example.com\npassword: hunter2\n"
    out = _find_match(body, "alice@example.com")
    assert out is not None
    kind, snippet = out
    assert kind == "sensitive"
    assert "[REDACTED]" in snippet
    assert "alice@example.com" not in snippet


def test_find_match_plain_when_no_sensitive_keyword():
    body = "Just a friendly mention of alice@example.com in passing."
    out = _find_match(body, "alice@example.com")
    assert out is not None
    kind, _ = out
    assert kind == "plain"


def test_find_match_returns_none_when_term_absent():
    assert _find_match("nothing to see here", "missing@example.com") is None


def _searx_payload(url: str, snippet: str = "") -> dict:
    return {
        "results": [
            {"url": url, "title": "leaked dump", "content": snippet, "engine": "google"}
        ]
    }


@pytest.mark.asyncio
async def test_pastebin_search_yields_sensitive_finding():
    """Mocks one SearXNG hit + raw paste containing a password near the email."""
    paste_url = "https://pastebin.com/raw/AAAA1111"
    raw_body = (
        "config dump\n"
        "user=alice@example.com\n"
        "password=hunter2\n"
        "...end\n"
    )

    with respx.mock(assert_all_called=False) as router:
        # Match the SearXNG endpoint regardless of which site dork is queried.
        router.get(url__regex=r"http://searxng:8080/search.*").mock(
            return_value=httpx.Response(
                200,
                json=_searx_payload(paste_url, snippet="alice@example.com leak"),
            )
        )
        # Raw paste body fetch.
        router.get(paste_url).mock(
            return_value=httpx.Response(200, text=raw_body)
        )
        # Fallback for any other paste-site hosts (return empty results so we
        # don't generate noise from the other 5 site dorks).
        router.get(url__regex=r"https?://(?!searxng|pastebin\.com).*").mock(
            return_value=httpx.Response(404, text="")
        )

        collector = PastebinSearchCollector()
        findings = []
        async for f in collector.run(SearchInput(email="alice@example.com")):
            findings.append(f)

    assert findings, "expected at least one finding"
    sensitive = [f for f in findings if f.payload.get("match_kind") == "sensitive"]
    assert sensitive, f"expected a sensitive finding, got kinds={[f.payload.get('match_kind') for f in findings]}"
    f = sensitive[0]
    assert f.collector == "pastebin_search"
    assert f.category == "leak"
    assert f.entity_type == "Paste"
    assert f.url == paste_url
    assert f.payload["paste_url"] == paste_url
    assert f.payload["site"] == "pastebin.com"
    assert f.payload["input_kind"] == "email"
    assert "alice@example.com" not in f.payload["snippet"]
    assert "[REDACTED]" in f.payload["snippet"]


@pytest.mark.asyncio
async def test_pastebin_search_no_terms_yields_nothing():
    collector = PastebinSearchCollector()
    findings = [f async for f in collector.run(SearchInput())]
    assert findings == []


def test_paste_sites_cover_expected_targets():
    # Sanity: the listed sites match the spec.
    expected = {
        "pastebin.com",
        "ghostbin.com",
        "rentry.co",
        "hastebin.com",
        "0bin.net",
        "justpaste.it",
    }
    assert expected == set(PASTE_SITES)


def test_collector_not_registered():
    """The collector is intentionally not in the global registry."""
    from app.collectors.base import collector_registry

    assert collector_registry.by_name("pastebin_search") is None
