"""Hashnode public profile scrape."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class HashnodeCollector(Collector):
    name = "hashnode"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        url = f"https://hashnode.com/@{u}"
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
            blog_url = None
            blog_tag = soup.find("a", href=lambda h: h and ".hashnode.dev" in h)
            if blog_tag:
                blog_url = blog_tag.get("href")
            else:
                og_url = soup.find("meta", attrs={"property": "og:url"})
                if og_url:
                    blog_url = og_url.get("content")
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Hashnode: {name}", url=url, confidence=0.65,
                payload={"platform": "hashnode", "username": u, "name": name,
                         "bio": bio, "blog_url": blog_url},
            )
        finally:
            await c.aclose()
