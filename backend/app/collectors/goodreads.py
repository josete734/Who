"""Goodreads public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class GoodreadsCollector(Collector):
    name = "goodreads"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        url = f"https://www.goodreads.com/user/show/{u}"
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
            loc = soup.find(class_=re.compile(r"infoBoxRowItem"))
            if loc:
                location = loc.get_text(strip=True)
            text = soup.get_text(" ", strip=True)
            books_count = None
            m = re.search(r"([\d,]+)\s+books?", text, re.I)
            if m:
                books_count = m.group(1).replace(",", "")
            friends_count = None
            m2 = re.search(r"([\d,]+)\s+friends?", text, re.I)
            if m2:
                friends_count = m2.group(1).replace(",", "")
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Goodreads: {name}", url=url, confidence=0.65,
                payload={"platform": "goodreads", "username": u, "name": name,
                         "location": location, "books_count": books_count,
                         "friends_count": friends_count},
            )
        finally:
            await c.aclose()
