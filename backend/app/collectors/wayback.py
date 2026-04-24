"""Wayback Machine CDX API lookup."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class WaybackCollector(Collector):
    name = "wayback"
    category = "archive"
    needs = ("domain", "linkedin_url", "username", "email")
    timeout_seconds = 90
    description = "Archive.org snapshots of domain / profile URL."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        targets: list[str] = []
        if input.domain:
            targets.append(input.domain.lstrip("https://").lstrip("http://"))
        if input.linkedin_url:
            targets.append(input.linkedin_url)
        if input.username:
            targets.append(f"linkedin.com/in/{input.username}")
            targets.append(f"github.com/{input.username}")
            targets.append(f"twitter.com/{input.username}")
        if input.email:
            targets.append(f"pastebin.com/raw/*{input.email}*")

        async with client(timeout=25) as c:
            for t in targets[:6]:
                try:
                    r = await c.get(
                        "https://web.archive.org/cdx/search/cdx",
                        params={"url": t, "output": "json", "limit": 25, "filter": "statuscode:200"},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                rows = r.json() or []
                if len(rows) <= 1:
                    continue
                header, *data = rows
                for row in data[:10]:
                    ts, orig = row[1], row[2]
                    snap = f"https://web.archive.org/web/{ts}/{orig}"
                    yield Finding(
                        collector=self.name,
                        category="archive",
                        entity_type="WaybackSnapshot",
                        title=f"Wayback {ts[:8]}: {orig[:80]}",
                        url=snap,
                        confidence=0.6,
                        payload={"original": orig, "timestamp": ts, "target": t},
                    )
