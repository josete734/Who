"""Untappd public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class UntappdCollector(Collector):
    name = "untappd"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://untappd.com/user/{u}"
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
            location = None
            loc = soup.find(class_=re.compile(r"location"))
            if loc:
                location = loc.get_text(strip=True)
            total_beers = None
            total_uniq = None
            for stat in soup.select(".stats li, .stats .stat"):
                txt = stat.get_text(" ", strip=True).lower()
                m = re.search(r"([\d,]+)", txt)
                if not m:
                    continue
                val = m.group(1).replace(",", "")
                if "total" in txt and "beer" in txt:
                    total_beers = val
                elif "unique" in txt:
                    total_uniq = val
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Untappd: {name}", url=url, confidence=0.65,
                payload={"platform": "untappd", "username": u, "name": name,
                         "location": location, "total_beers": total_beers,
                         "total_uniq": total_uniq},
            )
        finally:
            await c.aclose()
