"""Letterboxd public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class LetterboxdCollector(Collector):
    name = "letterboxd"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://letterboxd.com/{u}/"
        c = await get_client("default")
        try:
            try:
                r = await c.get(url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code == 404 or r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            name_tag = soup.find("meta", attrs={"property": "og:title"})
            name = name_tag.get("content") if name_tag else u
            bio_tag = soup.find("meta", attrs={"name": "description"})
            bio = bio_tag.get("content") if bio_tag else None
            stats = {}
            for h in soup.select("h4.profile-statistic, .profile-statistic"):
                value_el = h.find(class_=re.compile(r"value"))
                label_el = h.find(class_=re.compile(r"definition|label"))
                if value_el and label_el:
                    stats[label_el.get_text(strip=True).lower()] = value_el.get_text(strip=True)
            films = stats.get("films")
            lists_count = stats.get("lists")
            location = None
            loc = soup.find("span", class_=re.compile(r"location"))
            if loc:
                location = loc.get_text(strip=True)
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Letterboxd: {name}", url=url, confidence=0.65,
                payload={"platform": "letterboxd", "username": u, "name": name, "bio": bio,
                         "films_watched_count": films, "lists_count": lists_count, "location": location},
            )
        finally:
            await c.aclose()
