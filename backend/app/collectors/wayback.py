"""Wayback Machine CDX API lookup."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

logger = logging.getLogger("osint.collectors.wayback")


@register
class WaybackCollector(Collector):
    name = "wayback"
    category = "archive"
    needs = ("domain", "linkedin_url", "username", "email")
    # CDX API can be slow; previous 30s default timed out frequently.
    timeout_seconds = 60
    max_retries = 1  # one retry on transient HTTP / read timeouts
    description = "Archive.org snapshots of domain / profile URL."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        targets: list[str] = []
        if input.domain:
            # ``lstrip`` strips characters, not prefixes — use removeprefix.
            d = input.domain
            for prefix in ("https://", "http://"):
                if d.startswith(prefix):
                    d = d[len(prefix):]
            targets.append(d.rstrip("/"))
        if input.linkedin_url:
            targets.append(input.linkedin_url)
        if input.username:
            targets.append(f"linkedin.com/in/{input.username}")
            targets.append(f"github.com/{input.username}")
            targets.append(f"twitter.com/{input.username}")
        # NOTE: email + pastebin wildcard query was removed — it routinely
        # caused 30s+ timeouts at the CDX server with near-zero hit rate.

        # De-dup while preserving order.
        seen: set[str] = set()
        targets = [t for t in targets if t and not (t in seen or seen.add(t))]

        # Per-request timeout is short; the resilience wrapper provides the
        # outer wall clock and the retry loop here covers transient blips.
        async with client(timeout=20) as c:
            for t in targets[:6]:
                rows: list | None = None
                for attempt in range(2):  # one retry with backoff
                    try:
                        r = await c.get(
                            "https://web.archive.org/cdx/search/cdx",
                            params={
                                "url": t,
                                "output": "json",
                                "limit": 25,
                                "filter": "statuscode:200",
                                "fl": "timestamp,original",  # narrow response
                            },
                        )
                    except (httpx.HTTPError, asyncio.TimeoutError) as e:
                        logger.info(
                            "wayback request failed",
                            extra={"collector": self.name, "target": t,
                                   "attempt": attempt + 1, "error": type(e).__name__},
                        )
                        if attempt == 0:
                            await asyncio.sleep(1.5)
                            continue
                        rows = None
                        break
                    if r.status_code != 200:
                        break
                    try:
                        rows = r.json() or []
                    except ValueError:
                        rows = None
                    break
                if not rows or len(rows) <= 1:
                    continue
                _header, *data = rows
                for row in data[:10]:
                    if not row or len(row) < 2:
                        continue
                    ts, orig = row[0], row[1]
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
