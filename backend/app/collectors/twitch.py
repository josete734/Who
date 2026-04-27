"""Twitch public profile scrape (HTML/og: meta tags)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class TwitchCollector(Collector):
    name = "twitch"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://www.twitch.tv/{u}"
        c = await get_client("default")
        try:
            try:
                r = await c.get(url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code == 404 or r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            def meta(prop: str) -> str | None:
                tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
                return tag.get("content") if tag else None
            display_name = meta("og:title") or u
            bio = meta("og:description") or meta("description")
            image = meta("og:image")
            text = soup.get_text(" ", strip=True)
            mfo = re.search(r"([\d,\.]+)\s+follower", text, re.I)
            mfg = re.search(r"following\s+([\d,\.]+)", text, re.I)
            follower_count = int(mfo.group(1).replace(",", "").replace(".", "")) if mfo else None
            follow_count = int(mfg.group(1).replace(",", "").replace(".", "")) if mfg else None
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Twitch: {display_name}", url=url, confidence=0.65,
                payload={"platform": "twitch", "username": u, "display_name": display_name, "bio": bio,
                         "follower_count": follower_count, "follow_count": follow_count, "profile_image": image},
            )
        finally:
            await c.aclose()
