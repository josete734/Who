"""Resolve any IP found in extra_context via ipinfo.io (no key, limited)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

IP_RX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@register
class IpInfoCollector(Collector):
    name = "ipinfo"
    category = "infra"
    needs = ("extra_context",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.extra_context:
            return
        ips = set(IP_RX.findall(input.extra_context))
        if not ips:
            return
        async with client(timeout=8) as c:
            for ip in list(ips)[:5]:
                try:
                    r = await c.get(f"https://ipinfo.io/{ip}/json")
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                d = r.json()
                yield Finding(
                    collector=self.name,
                    category="infra",
                    entity_type="IPLocation",
                    title=f"IP {ip}: {d.get('city','?')}, {d.get('country','?')} ({d.get('org','')})",
                    url=f"https://ipinfo.io/{ip}",
                    confidence=0.8,
                    payload=d,
                )
