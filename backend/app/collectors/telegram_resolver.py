"""Telegram resolver — public profile/channel scrape via t.me HTML.

Scrapes ``https://t.me/{username}`` for the visible name, photo and bio. Does
not require authentication. Complements ``telegram_public`` (which uses OG
tags only) by extracting the rendered ``tgme_page_*`` blocks.

Phone -> profile resolution requires the official MTProto protocol (Telethon)
which in turn requires API credentials; that path is intentionally left as a
stub that activates only when ``TELEGRAM_API_ID`` and ``TELEGRAM_API_HASH``
env vars are present. By default the collector runs the HTML scraper only.
"""
from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


def _pick(html: str, pat: str) -> str | None:
    m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


@register
class TelegramResolverCollector(Collector):
    name = "telegram_resolver"
    category = "social"
    needs = ("username",)
    timeout_seconds = 15
    description = "Telegram public profile resolver via t.me HTML scraping."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@").strip()
        if not u:
            return
        url = f"https://t.me/{u}"

        c = await get_client("gentle")
        try:
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                return
        finally:
            await c.aclose()
        if r.status_code != 200:
            return
        html = r.text
        if "tgme_page" not in html:
            return

        # Visible name: <div class="tgme_page_title"><span>Name</span></div>
        name = _pick(html, r'tgme_page_title[^>]*>\s*<span[^>]*>([^<]+)</span>')
        photo = _pick(html, r'<img[^>]+class="tgme_page_photo_image"[^>]+src="([^"]+)"')
        bio = _pick(html, r'<div class="tgme_page_description"[^>]*>(.+?)</div>')
        if bio:
            bio = re.sub(r"<[^>]+>", " ", bio)
            bio = re.sub(r"\s+", " ", bio).strip()[:600]
        members = _pick(html, r'tgme_page_extra"[^>]*>([^<]+)</div>')

        # Optional Telethon stub for phone -> entity (disabled by default).
        # Activate by setting TELEGRAM_API_ID and TELEGRAM_API_HASH; when wired
        # this branch would call:
        #     from telethon import TelegramClient
        #     async with TelegramClient(StringSession(...), api_id, api_hash) as tg:
        #         entity = await tg.get_entity(input.phone)
        # Left commented to avoid pulling Telethon as a hard dependency.
        _telethon_enabled = bool(
            os.environ.get("TELEGRAM_API_ID") and os.environ.get("TELEGRAM_API_HASH")
        )
        # if _telethon_enabled and input.phone:
        #     ...

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="TelegramProfile",
            title=f"Telegram: {name or u}",
            url=url,
            confidence=0.75,
            payload={
                "handle": u,
                "name": name,
                "photo_url": photo,
                "bio": bio,
                "members_or_extra": members,
                "telethon_enabled": _telethon_enabled,
            },
        )


__all__ = ["TelegramResolverCollector"]
