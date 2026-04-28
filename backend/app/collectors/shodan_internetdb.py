"""Shodan InternetDB: free, keyless, read-only lookup by IP.

Wave 1 fix: drop the premium SHODAN_API_KEY branch (paid external API) and
replace the synchronous ``socket.gethostbyname`` with an async resolver so the
collector does not block the event loop while looking up A records.
"""
from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

IP_RX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


async def _resolve_async(host: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
    except (OSError, socket.gaierror):
        return None
    for family, _type, _proto, _canon, sockaddr in infos:
        if family == socket.AF_INET and sockaddr:
            return sockaddr[0]
    return None


@register
class ShodanInternetDBCollector(Collector):
    name = "shodan_internetdb"
    category = "infra"
    needs = ("domain", "extra_context")
    timeout_seconds = 20
    description = "Shodan InternetDB (free, no key) — IP/domain exposed services."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        targets: list[str] = []
        if input.extra_context:
            targets += IP_RX.findall(input.extra_context)
        if input.domain:
            ip = await _resolve_async(input.domain.strip())
            if ip:
                targets.append(ip)

        if not targets:
            return

        async with client(timeout=12) as c:
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
