"""urlscan.io public search — free tier without key; higher limits with key."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.dynamic_settings import get_runtime
from app.http_util import client
from app.schemas import SearchInput


@register
class URLScanCollector(Collector):
    name = "urlscan"
    category = "domain"
    needs = ("domain", "email")
    timeout_seconds = 25
    description = "urlscan.io search: past scans by domain or email."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        rt = await get_runtime()
        key = rt.get("URLSCAN_API_KEY") or ""
        headers = {"API-Key": key} if key else {}

        queries: list[str] = []
        if input.domain:
            queries.append(f'domain:{input.domain.strip()}')
        if input.email:
            queries.append(f'page.url:"{input.email}"')

        async with client(timeout=20, headers=headers) as c:
            for q in queries:
                try:
                    r = await c.get("https://urlscan.io/api/v1/search/", params={"q": q, "size": 20})
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                results = (r.json() or {}).get("results", [])
                for it in results[:15]:
                    page = it.get("page", {}) or {}
                    yield Finding(
                        collector=self.name,
                        category="domain",
                        entity_type="URLScanResult",
                        title=f"urlscan: {page.get('url', '')[:150]}",
                        url=it.get("result"),
                        confidence=0.6,
                        payload={
                            "scan_id": it.get("_id"),
                            "domain": page.get("domain"),
                            "ip": page.get("ip"),
                            "country": page.get("country"),
                            "server": page.get("server"),
                            "status": page.get("status"),
                            "time": it.get("task", {}).get("time"),
                        },
                    )
