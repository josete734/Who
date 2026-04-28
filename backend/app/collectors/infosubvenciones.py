"""BDNS — Base de Datos Nacional de Subvenciones (Wave 8).

The Spanish "Base de Datos Nacional de Subvenciones" publishes every
public grant (≥3.000 €) along with the recipient's name and ID, mandated
by art. 18 of the Ley General de Subvenciones. The portal exposes a
public REST API at ``infosubvenciones.es/bdnstrans/`` that responds with
JSON; no key required.

We hit ``/api/concesiones`` filtered by beneficiary text — the most common
entry point for OSINT enrichment on someone whose name shows up in a
subvenciones listing.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_API = "https://www.infosubvenciones.es/bdnstrans/api/concesiones/busqueda"


@register
class InfoSubvencionesCollector(Collector):
    name = "infosubvenciones"
    category = "es_official"
    needs = ("full_name",)
    timeout_seconds = 30
    description = "BDNS — concesiones de subvenciones públicas (≥3 000 €)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        seen: set[str] = set()
        async with client(timeout=20) as c:
            for name in input.name_variants():
                params = {
                    "descripcion": name,
                    "size": 30,
                    "page": 0,
                    "vpd": "GE",  # all administrations
                }
                try:
                    r = await c.get(_API, params=params, headers={"Accept": "application/json"})
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                content = data.get("content") or data.get("results") or []
                for it in content[:30]:
                    if not isinstance(it, dict):
                        continue
                    bene = (it.get("beneficiario") or {}).get("nombre") or it.get("beneficiario_nombre")
                    if not bene:
                        continue
                    nif = (it.get("beneficiario") or {}).get("nif") or it.get("beneficiario_nif")
                    importe = it.get("importe") or it.get("importeConcedido")
                    fecha = it.get("fechaConcesion") or it.get("fecha_concesion")
                    organismo = it.get("organismo")
                    fp = f"{bene}|{nif}|{fecha}|{importe}"
                    if fp in seen:
                        continue
                    seen.add(fp)

                    title_l = str(bene).lower()
                    name_l = name.lower()
                    if name_l in title_l:
                        confidence = 0.85
                    elif any(t in title_l for t in name_l.split() if len(t) >= 4):
                        confidence = 0.55
                    else:
                        continue

                    yield Finding(
                        collector=self.name,
                        category="es_official",
                        entity_type="Subvencion",
                        title=f"Subvención: {bene} — {organismo or '?'} ({importe or '?'} €)",
                        url=None,
                        confidence=confidence,
                        payload={
                            "beneficiario": bene,
                            "nif": nif,
                            "importe": importe,
                            "fecha": fecha,
                            "organismo": organismo,
                            "raw": it,
                        },
                    )
