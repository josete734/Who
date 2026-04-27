"""Tests for telegram_resolver collector."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.telegram_resolver import TelegramResolverCollector
from app.schemas import SearchInput


_HTML = """
<html><body>
<div class="tgme_page">
  <img class="tgme_page_photo_image" src="https://cdn.t.me/photo.jpg" />
  <div class="tgme_page_title"><span>Jane Doe</span></div>
  <div class="tgme_page_description">Bio about <b>Jane</b></div>
  <div class="tgme_page_extra">1 234 subscribers</div>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_telegram_resolver_parses_profile():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://t.me/janedoe").mock(return_value=httpx.Response(200, text=_HTML))
        findings = [
            f async for f in TelegramResolverCollector().run(SearchInput(username="janedoe"))
        ]
    assert len(findings) == 1
    f = findings[0]
    assert f.entity_type == "TelegramProfile"
    assert f.payload["name"] == "Jane Doe"
    assert f.payload["photo_url"].endswith("photo.jpg")
    assert "Jane" in (f.payload["bio"] or "")


@pytest.mark.asyncio
async def test_telegram_resolver_no_tgme_marker():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://t.me/ghost").mock(return_value=httpx.Response(200, text="<html></html>"))
        findings = [
            f async for f in TelegramResolverCollector().run(SearchInput(username="ghost"))
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_telegram_resolver_http_error():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://t.me/x").mock(side_effect=httpx.ConnectError("boom"))
        findings = [f async for f in TelegramResolverCollector().run(SearchInput(username="x"))]
    assert findings == []
