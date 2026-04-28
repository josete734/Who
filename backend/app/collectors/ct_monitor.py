"""Certificate Transparency monitor — collector mode.

Per-case snapshot of recent issuances for a domain via the certspotter
free-tier issuances API. Yields one Finding per certificate (subdomain,
issuer, validity window, certspotter id).

The complementary long-running watcher lives at ``app.ct_watcher.runner``
and reuses the same HTTP shape with an ``after=`` cursor.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"


@register
class CTMonitorCollector(Collector):
    name = "ct_monitor"
    category = "domain"
    needs = ("domain",)
    timeout_seconds = 30
    description = (
        "Certificate Transparency snapshot for a domain via certspotter "
        "(subdomains, issuers, validity windows)."
    )

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        domain = input.domain.strip(".").lower()
        params = {
            "domain": domain,
            "include_subdomains": "true",
            "expand": "dns_names,issuer,cert",
        }
        async with client(timeout=25) as c:
            try:
                r = await c.get(CERTSPOTTER_URL, params=params)
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                return

        if not isinstance(data, list):
            return

        seen: set[str] = set()
        for row in data[:1000]:
            cs_id = str(row.get("id") or "")
            issuer = ((row.get("issuer") or {}).get("name")) or row.get("issuer_name")
            valid_from = row.get("not_before")
            valid_to = row.get("not_after")
            for name in row.get("dns_names") or []:
                sub = (name or "").strip().lower().lstrip("*.")
                if not sub or sub in seen:
                    continue
                seen.add(sub)
                yield Finding(
                    collector=self.name,
                    category="domain",
                    entity_type="Subdomain",
                    title=sub,
                    url=f"https://api.certspotter.com/v1/issuances/{cs_id}" if cs_id else None,
                    confidence=0.9,
                    payload={
                        "subdomain": sub,
                        "issuer": issuer,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "certspotter_id": cs_id,
                    },
                )


# WIRING: do NOT add this collector to any explicit registry list — it self-
# registers via @register on import. To actually run it the orchestrator must
# import ``app.collectors.ct_monitor`` (e.g. from app.collectors.__init__).
# That import is intentionally left unwired here.
