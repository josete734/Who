"""Passive DNS — historical IP / subdomain enumeration (Wave 8).

Two free, no-key endpoints chained:

* ``https://api.hackertarget.com/hostsearch/?q={domain}`` — 100 req/day
  per source IP, returns subdomain + IP rows in plain text.
* ``https://api.threatminer.org/v2/domain.php?q={domain}&rt=2`` — passive
  DNS of historical IP resolutions (no key, gentle rate limit).

Each unique subdomain or IP becomes a ``Finding``; the dispatcher's pivot
extractor picks them up automatically and feeds them downstream.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_HACKERTARGET = "https://api.hackertarget.com/hostsearch/"
_THREATMINER = "https://api.threatminer.org/v2/domain.php"


@register
class PassiveDNSCollector(Collector):
    name = "passive_dns"
    category = "infra"
    needs = ("domain",)
    timeout_seconds = 25
    description = "Passive DNS via HackerTarget + ThreatMiner (no API key)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        d = input.domain.strip().lower()
        if d.startswith("http"):
            d = d.split("://", 1)[1].split("/", 1)[0]

        seen_subs: set[str] = set()
        seen_ips: set[str] = set()

        async with client(timeout=15) as c:
            # 1) HackerTarget — text/csv
            try:
                r = await c.get(_HACKERTARGET, params={"q": d})
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200 and r.text:
                # Skip the "API count exceeded" text response.
                body = r.text.strip()
                if not body.lower().startswith("api count exceeded") and "," in body:
                    for line in body.splitlines():
                        sub, _, ip = line.partition(",")
                        sub = sub.strip().lower()
                        ip = ip.strip()
                        if sub and sub != d and sub not in seen_subs:
                            seen_subs.add(sub)
                            yield Finding(
                                collector=self.name,
                                category="infra",
                                entity_type="Subdomain",
                                title=f"Subdominio: {sub}",
                                url=f"https://{sub}",
                                confidence=0.85,
                                payload={
                                    "subdomain": sub,
                                    "ip": ip,
                                    "source": "hackertarget",
                                    "domain": d,
                                },
                            )
                        if ip and ip not in seen_ips:
                            seen_ips.add(ip)

            # 2) ThreatMiner — passive DNS JSON
            try:
                r2 = await c.get(_THREATMINER, params={"q": d, "rt": 2})
            except httpx.HTTPError:
                r2 = None
            if r2 is not None and r2.status_code == 200:
                try:
                    data = r2.json()
                except ValueError:
                    data = {}
                results = (data.get("results") or []) if isinstance(data, dict) else []
                for item in results[:100]:
                    if not isinstance(item, dict):
                        continue
                    ip = item.get("ip") or item.get("ip_address")
                    last_seen = item.get("last_seen")
                    if ip and ip not in seen_ips:
                        seen_ips.add(ip)
                        yield Finding(
                            collector=self.name,
                            category="infra",
                            entity_type="HistoricalIP",
                            title=f"IP histórica de {d}: {ip}",
                            url=None,
                            confidence=0.75,
                            payload={
                                "ip": ip,
                                "last_seen": last_seen,
                                "source": "threatminer",
                                "domain": d,
                            },
                        )
