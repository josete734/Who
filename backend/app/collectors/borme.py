"""BORME (Boletín Oficial del Registro Mercantil) search.

The BOE offers a free endpoint that we query by full name to find commercial
register appearances (appointments, cessations, constitutions). We use the
open search UI endpoint and parse results.
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
class BORMECollector(Collector):
    name = "borme"
    category = "es_official"
    needs = ("full_name", "birth_name", "aliases")
    timeout_seconds = 45
    description = "BORME: cargos y sociedades asociadas al nombre."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        seen: set[str] = set()
        async with client(timeout=30) as c:
            for name in input.name_variants():
                q = quote_plus(name)
                url = f"https://www.boe.es/buscar/borme.php?campo[0]=TITULO&dato[0]=&operador[0]=and&campo[1]=ID_BORME&dato[1]=&operador[1]=and&campo[2]=TEXT&dato[2]={q}&campo[3]=&dato[3]=&operador[3]=and&page_hits=40"
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                for item in soup.select("ul.resultados-busqueda li, li.dispo, ul.sumario li")[:40]:
                    a = item.find("a", href=True)
                    if not a:
                        continue
                    link = a["href"]
                    if link.startswith("/"):
                        link = f"https://www.boe.es{link}"
                    if link in seen:
                        continue
                    seen.add(link)
                    title = a.get_text(strip=True)
                    if not title:
                        continue
                    ctx = item.get_text(" ", strip=True)[:400]
                    # Identity guard: BORME's search index returns whole pages
                    # whose titles often don't mention the subject; the actual
                    # mention is in the surrounding context. Require the name
                    # (or a 4+ char token of it) to appear in title OR ctx.
                    haystack = f"{title} {ctx}".lower()
                    name_l = name.lower()
                    name_tokens = [t for t in name_l.split() if len(t) >= 4]
                    if name_l in haystack:
                        confidence = 0.75
                    elif name_tokens and any(t in haystack for t in name_tokens):
                        confidence = 0.55
                    else:
                        continue
                    yield Finding(
                        collector=self.name,
                        category="es_official",
                        entity_type="BORMEEntry",
                        title=f"BORME: {title[:180]}",
                        url=link,
                        confidence=confidence,
                        payload={"raw": ctx, "name_queried": name, "city_hint": input.city},
                    )
