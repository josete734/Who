"""LeakIX — services / leaks indexer. Free tier accepts anonymous limited queries
when key is absent; API key raises quotas."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.dynamic_settings import get_runtime
from app.http_util import client
from app.schemas import SearchInput


@register
class LeakIXCollector(Collector):
    name = "leakix"
    category = "infra"
    needs = ("domain", "email")
    timeout_seconds = 20
    description = "LeakIX: exposed services / leaks by domain or email."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        rt = await get_runtime()
        key = rt.get("LEAKIX_API_KEY") or ""
        headers = {"Accept": "application/json"}
        if key:
            headers["api-key"] = key

        queries: list[tuple[str, str]] = []
        if input.domain:
            queries.append(("host", input.domain.strip()))
        if input.email:
            queries.append(("email", input.email.strip()))

        async with client(timeout=15, headers=headers) as c:
            for scope, value in queries:
                try:
                    r = await c.get(f"https://leakix.net/search?scope={scope}&q={value}")
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                rows = data if isinstance(data, list) else data.get("Services") or data.get("Leaks") or []
                for it in rows[:15]:
                    if not isinstance(it, dict):
                        continue
                    yield Finding(
                        collector=self.name,
                        category="infra",
                        entity_type="LeakIXRecord",
                        title=f"LeakIX {scope}: {(it.get('host') or it.get('ip') or it.get('service') or '')[:140]}",
                        url=f"https://leakix.net/host/{it.get('host') or it.get('ip') or ''}",
                        confidence=0.6,
                        payload={k: it.get(k) for k in ("host", "ip", "port", "protocol", "summary", "tags", "time") if k in it},
                    )
