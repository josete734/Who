"""Shodan InternetDB: free, keyless, read-only lookup by IP.

Plus optional Shodan host search when SHODAN_API_KEY is set.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.dynamic_settings import get_runtime
from app.http_util import client
from app.schemas import SearchInput

IP_RX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@register
class ShodanInternetDBCollector(Collector):
    name = "shodan_internetdb"
    category = "infra"
    needs = ("domain", "extra_context")
    timeout_seconds = 20
    description = "Shodan InternetDB (free, no key) — IP/domain exposed services."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        async with client(timeout=12) as c:
            targets: list[str] = []
            if input.extra_context:
                targets += IP_RX.findall(input.extra_context)
            if input.domain:
                # Resolve domain to IP
                try:
                    import socket
                    ip = socket.gethostbyname(input.domain.strip())
                    targets.append(ip)
                except OSError:
                    pass

            for ip in list(dict.fromkeys(targets))[:5]:
                try:
                    r = await c.get(f"https://internetdb.shodan.io/{ip}")
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                d = r.json()
                yield Finding(
                    collector=self.name,
                    category="infra",
                    entity_type="ShodanHost",
                    title=f"Shodan {ip}: {len(d.get('ports', []))} puertos, {len(d.get('vulns', []))} CVE",
                    url=f"https://www.shodan.io/host/{ip}",
                    confidence=0.85,
                    payload=d,
                )

        # Premium: if SHODAN_API_KEY available, do a facets search on domain
        rt = await get_runtime()
        key = rt.get("SHODAN_API_KEY") or ""
        if not key or not input.domain:
            return
        async with client(timeout=15) as c:
            try:
                r = await c.get(
                    "https://api.shodan.io/shodan/host/search",
                    params={"key": key, "query": f"hostname:{input.domain.strip()}", "limit": 10},
                )
            except httpx.HTTPError:
                return
            if r.status_code != 200:
                return
            for m in (r.json() or {}).get("matches", [])[:10]:
                yield Finding(
                    collector=self.name,
                    category="infra",
                    entity_type="ShodanMatch",
                    title=f"Shodan host: {m.get('ip_str')} ({m.get('port')}/{m.get('transport')})",
                    url=f"https://www.shodan.io/host/{m.get('ip_str')}",
                    confidence=0.8,
                    payload={k: m.get(k) for k in ("ip_str", "port", "transport", "product", "hostnames", "org", "location") if k in m},
                )
