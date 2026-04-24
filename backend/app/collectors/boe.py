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
                    yield Finding(
                        collector=self.name,
                        category="es_official",
                        entity_type="BOEEntry",
                        title=f"BOE: {title[:180]}",
                        url=href,
                        confidence=0.6,
                        payload={"name_queried": name, "city_hint": input.city},
                    )
