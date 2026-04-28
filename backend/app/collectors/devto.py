"""Dev.to public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class DevToCollector(Collector):
    name = "devto"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        url = f"https://dev.to/{u}"
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
            joined_date = None
            joined_el = soup.find(string=re.compile(r"Joined", re.I))
            if joined_el:
                m = re.search(r"Joined\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", str(joined_el))
                if m:
                    joined_date = m.group(1)
            posts = None
            text = soup.get_text(" ", strip=True)
            m2 = re.search(r"([\d,]+)\s+posts?\s+published", text, re.I)
            if m2:
                posts = m2.group(1).replace(",", "")
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"dev.to: {name}", url=url, confidence=0.65,
                payload={"platform": "devto", "username": u, "name": name,
                         "bio": bio, "joined_date": joined_date, "posts": posts},
            )
        finally:
            await c.aclose()
