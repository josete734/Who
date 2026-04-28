"""Substack publication about page scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class SubstackCollector(Collector):
    name = "substack"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        url = f"https://{u}.substack.com/about"
        c = await get_client("default")
        try:
            try:
                r = await c.get(url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            pub_tag = soup.find("meta", attrs={"property": "og:site_name"})
            publication_name = pub_tag.get("content") if pub_tag else u
            author_tag = soup.find("meta", attrs={"name": "author"})
            author = author_tag.get("content") if author_tag else None
            subscribers = None
            text = soup.get_text(" ", strip=True)
            m = re.search(r"([\d,]+)\s+(?:subscribers?|readers?)", text, re.I)
            if m:
                subscribers = m.group(1).replace(",", "")
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Substack: {publication_name}", url=url, confidence=0.65,
                payload={"platform": "substack", "username": u,
                         "publication_name": publication_name, "author": author,
                         "subscribers": subscribers},
            )
        finally:
            await c.aclose()
