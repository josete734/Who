"""Telegram public profile / channel existence via t.me OG tags.

Emits only if:
  - og:url contains t.me/<user> (case-insensitive), AND
  - tgme_page_title or tgme_channel_info block is present.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


def _pick(html: str, pat: str) -> str | None:
    m = re.search(pat, html)
    return m.group(1) if m else None


@register
class TelegramPublicCollector(Collector):
    name = "telegram_public"
    category = "social"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://t.me/{u}"
        async with client(timeout=10) as c:
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        html = r.text

        has_page_marker = "tgme_page_title" in html or "tgme_channel_info" in html or "tgme_page_description" in html
        og_url = _pick(html, r'<meta property="og:url" content="([^"]+)"') or ""
        if not has_page_marker:
            return
        # Must reference the actual handle to avoid falsely matching the t.me home / error pages.
        if f"/{u.lower()}" not in og_url.lower():
            return

        title = _pick(html, r'<meta property="og:title" content="([^"]+)"') or u
        desc = _pick(html, r'<meta property="og:description" content="([^"]+)"') or ""
        img = _pick(html, r'<meta property="og:image" content="([^"]+)"')

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="TelegramPresence",
            title=f"Telegram: {title[:150]}",
            url=url,
            confidence=0.8,
            payload={
                "handle": u,
                "og_title": title,
                "og_description": desc[:500],
                "og_image": img,
                "note": "Match por og:url que contiene el handle. Verifica que sea la persona (un alias no equivale a identidad).",
            },
        )
