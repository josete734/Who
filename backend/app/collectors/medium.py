"""Medium public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class MediumCollector(Collector):
    name = "medium"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://medium.com/@{u}"
        c = await get_client("default")
        try:
            try:
                r = await c.get(url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            name_tag = soup.find("meta", attrs={"property": "og:title"})
            name = name_tag.get("content") if name_tag else u
            bio_tag = soup.find("meta", attrs={"name": "description"})
            bio = bio_tag.get("content") if bio_tag else None
            text = soup.get_text(" ", strip=True)
            followers = None
            m = re.search(r"([\d.,KkMm]+)\s+Followers?", text)
            if m:
                followers = m.group(1)
            posts = None
            m2 = re.search(r"([\d,]+)\s+(?:stories|posts?)", text, re.I)
            if m2:
                posts = m2.group(1).replace(",", "")
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Medium: {name}", url=url, confidence=0.65,
                payload={"platform": "medium", "username": u, "name": name,
                         "bio": bio, "followers": followers, "posts": posts},
            )
        finally:
            await c.aclose()
