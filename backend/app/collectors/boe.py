"""BOE (Boletín Oficial del Estado) full-text search by name.

Returns appointments, sanctions, civil-service, awards, subpoenas, etc.
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
class BOECollector(Collector):
    name = "boe"
    category = "es_official"
    needs = ("full_name", "birth_name", "aliases")
    timeout_seconds = 45
    description = "BOE: menciones oficiales del nombre (nombramientos, sanciones, edictos)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        seen: set[str] = set()
        async with client(timeout=30) as c:
            for name in input.name_variants():
                q = quote_plus(name)
                url = f"https://www.boe.es/buscar/doc.php?coleccion=iberlex&text={q}&page_hits=40"
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.select("ul.resultados-busqueda li a[href], div.dispo a[href]")[:40]:
                    href = a.get("href", "")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://www.boe.es{href}"
                    if href in seen:
                        continue
                    seen.add(href)
                    title = a.get_text(strip=True)
                    if not title:
                        continue
                    # Identity guard: require the queried name (or one of its
                    # tokens of length >=4) to actually appear in the result
                    # title. Drops generic table-of-contents links that match
                    # the search index but don't mention the subject.
                    title_l = title.lower()
                    name_l = name.lower()
                    name_tokens = [t for t in name_l.split() if len(t) >= 4]
                    if name_l in title_l:
                        confidence = 0.7
                    elif name_tokens and any(t in title_l for t in name_tokens):
                        confidence = 0.55
                    else:
                        # Title doesn't actually reference the subject — skip,
                        # don't pollute the findings with low-signal noise.
                        continue
                    yield Finding(
                        collector=self.name,
                        category="es_official",
                        entity_type="BOEEntry",
                        title=f"BOE: {title[:180]}",
                        url=href,
                        confidence=confidence,
                        payload={"name_queried": name, "city_hint": input.city},
                    )
