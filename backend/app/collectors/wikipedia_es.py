"""Wikipedia ES/EN opensearch by full name."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class WikipediaCollector(Collector):
    name = "wikipedia"
    category = "knowledge"
    needs = ("full_name", "birth_name", "aliases")
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        variants = input.name_variants()
        if not variants:
            return
        async with client(timeout=12) as c:
            for name in variants:
                async for f in self._search(c, name):
                    yield f

    async def _search(self, c, name: str) -> AsyncIterator[Finding]:
        for lang in ("es", "en"):
                try:
                    r = await c.get(
                        f"https://{lang}.wikipedia.org/w/api.php",
                        params={
                            "action": "opensearch",
                            "search": name,
                            "limit": 5,
                            "namespace": 0,
                            "format": "json",
                        },
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                    titles, descs, urls = data[1], data[2], data[3]
                except (ValueError, IndexError):
                    continue
                for t, d, url in zip(titles, descs, urls):
                    yield Finding(
                        collector=self.name,
                        category="name",
                        entity_type=f"Wikipedia{lang.upper()}",
                        title=f"Wikipedia ({lang}): {t}",
                        url=url,
                        confidence=0.55,
                        payload={"language": lang, "description": d},
                    )
