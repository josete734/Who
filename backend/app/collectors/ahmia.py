"""Ahmia.fi — search the Tor (.onion) public index from the clearnet.

No key. The service returns HTML; we parse the result blocks heuristically.
Inspired by TorBot (DedSecInside) but without running a Tor container.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class AhmiaCollector(Collector):
    name = "ahmia"
    category = "dark_web"
    needs = ("full_name", "birth_name", "aliases", "email", "username", "phone")
    timeout_seconds = 25
    description = "Ahmia: index público de servicios .onion."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        queries: list[str] = []
        for n in input.name_variants():
            queries.append(f'"{n}"')
        if input.email: queries.append(f'"{input.email}"')
        if input.username: queries.append(f'"{input.username.lstrip("@")}"')
        if input.phone: queries.append(f'"{input.phone.lstrip("+")}"')

        async with client(timeout=18) as c:
            for q in queries[:5]:
                url = f"https://ahmia.fi/search/?q={quote_plus(q)}"
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                for item in soup.select("li.result")[:10]:
                    h4 = item.find(["h4", "h3"])
                    a = item.find("a", href=True)
                    if not (h4 and a):
                        continue
                    title = h4.get_text(strip=True)
                    href = a["href"]
                    snippet = item.get_text(" ", strip=True)[:300]
                    yield Finding(
                        collector=self.name,
                        category="dark_web",
                        entity_type="OnionMention",
                        title=f"Ahmia: {title[:140]}",
                        url=href,
                        confidence=0.5,
                        payload={"query": q, "snippet": snippet},
                    )
