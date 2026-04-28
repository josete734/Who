"""Spanish "Portal de Transparencia" search (Wave 8).

The transparency portal at ``transparencia.gob.es`` exposes an REST search
endpoint over the published declarations (cargos públicos, conflicts of
interest, asset declarations). The endpoint is documented in the open-data
catalogue and does not require an API key.

We search by full name and surface the top hits as ``TransparenciaEntry``
findings. Base legitimadora: art. 18 LGS / Ley 19/2013 (publication of
public officials' data is mandated by law).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_SEARCH = "https://transparencia.gob.es/servicios-buscador/buscar.htm"


@register
class TransparenciaESCollector(Collector):
    name = "transparencia_es"
    category = "es_official"
    needs = ("full_name",)
    timeout_seconds = 30
    description = "Portal de Transparencia (España) — cargos públicos, declaraciones, conflictos."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        seen: set[str] = set()
        async with client(timeout=20) as c:
            for name in input.name_variants():
                params = {
                    "categoria": "Subvenciones",
                    "lang": "es",
                    "buscar": name,
                    "num_id": "1",
                }
                try:
                    r = await c.get(_SEARCH, params=params)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200 or not r.text:
                    continue
                # The endpoint returns HTML; extract anchors that point to
                # /servicios-buscador/contenido/... pages — those are the
                # individual records.
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.select("a[href*='/servicios-buscador/contenido/']")[:30]:
                    href = a.get("href") or ""
                    if href.startswith("/"):
                        href = f"https://transparencia.gob.es{href}"
                    if href in seen:
                        continue
                    seen.add(href)
                    title = a.get_text(" ", strip=True)
                    if not title or len(title) < 6:
                        continue
                    title_l = title.lower()
                    name_l = name.lower()
                    if name_l in title_l:
                        confidence = 0.8
                    elif any(t in title_l for t in name_l.split() if len(t) >= 4):
                        confidence = 0.55
                    else:
                        continue
                    yield Finding(
                        collector=self.name,
                        category="es_official",
                        entity_type="TransparenciaEntry",
                        title=f"Transparencia: {title[:200]}",
                        url=href,
                        confidence=confidence,
                        payload={"name_queried": name, "source": "transparencia.gob.es"},
                    )
