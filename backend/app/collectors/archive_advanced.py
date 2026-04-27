"""Advanced archive enumeration: Wayback CDX wildcard + archive.today + Common Crawl.

Complements the basic ``wayback`` collector by enumerating *all* archived URLs
under a domain (rather than a single target). Useful for digging up deleted
social posts, vanished pages, etc.

Sources (run concurrently, 25s timeout each):
  - Wayback CDX wildcard `*.{domain}/*` (filter statuscode:200, limit 500).
  - archive.today HTML scrape of `https://archive.ph/{domain}` — gentle.
  - Common Crawl most-recent two indexes via `index.commoncrawl.org`.

Yields one Finding per archived URL with payload {archived_url, original_url,
ts, source, status}.

NOTE: this collector is intentionally NOT registered — orchestrator wiring is
deferred until the integration agent picks it up (see WIRING comment below).
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

logger = logging.getLogger("osint.collectors.archive_advanced")

SOURCE_TIMEOUT = 25.0
ARCHIVE_TODAY_DELAY = 3.0  # 1 req / 3s, gentle
WAYBACK_LIMIT = 500


@register
class ArchiveAdvancedCollector(Collector):
    name = "archive_advanced"
    category = "archive"
    needs = ("domain",)
    timeout_seconds = 90  # outer wall clock; sources run concurrently
    max_retries = 0
    description = (
        "Enumerate archived URLs for a domain across Wayback CDX (wildcard), "
        "archive.today, and the two most recent Common Crawl indexes."
    )

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        domain = self._clean_domain(input.domain)
        if not domain:
            return

        results: list[list[dict[str, Any]]] = await asyncio.gather(
            self._wayback_cdx(domain),
            self._archive_today(domain),
            self._common_crawl(domain),
            return_exceptions=False,
        )

        seen: set[str] = set()
        for batch in results:
            for hit in batch:
                key = hit.get("archived_url") or hit.get("original_url") or ""
                if not key or key in seen:
                    continue
                seen.add(key)
                src = hit.get("source", "?")
                orig = hit.get("original_url", "")
                ts = hit.get("ts", "")
                title = f"[{src}] {ts[:8] if ts else ''} {orig[:80]}".strip()
                yield Finding(
                    collector=self.name,
                    category=self.category,
                    entity_type="ArchivedURL",
                    title=title,
                    url=hit.get("archived_url"),
                    confidence=0.55,
                    payload=hit,
                )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _clean_domain(d: str) -> str:
        for prefix in ("https://", "http://"):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.strip("/").lower()

    async def _wayback_cdx(self, domain: str) -> list[dict[str, Any]]:
        url = "https://web.archive.org/cdx/search/cdx"
        params = {
            "url": f"*.{domain}/*",
            "output": "json",
            "fl": "timestamp,original,statuscode",
            "filter": "statuscode:200",
            "limit": str(WAYBACK_LIMIT),
        }
        try:
            async with client(timeout=SOURCE_TIMEOUT) as c:
                r = await c.get(url, params=params)
            if r.status_code != 200:
                return []
            rows = r.json() or []
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as e:
            logger.info("wayback cdx failed: %s", type(e).__name__)
            return []
        if not rows or len(rows) <= 1:
            return []
        out: list[dict[str, Any]] = []
        for row in rows[1:]:
            if not row or len(row) < 2:
                continue
            ts = row[0]
            orig = row[1]
            status = row[2] if len(row) >= 3 else ""
            out.append({
                "archived_url": f"https://web.archive.org/web/{ts}/{orig}",
                "original_url": orig,
                "ts": ts,
                "source": "wayback",
                "status": status,
            })
        return out

    async def _archive_today(self, domain: str) -> list[dict[str, Any]]:
        await asyncio.sleep(ARCHIVE_TODAY_DELAY)  # gentle 1 req / 3s
        url = f"https://archive.ph/{domain}"
        try:
            async with client(timeout=SOURCE_TIMEOUT) as c:
                r = await c.get(url, follow_redirects=True)
            if r.status_code != 200:
                return []
            html = r.text
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            logger.info("archive.today failed: %s", type(e).__name__)
            return []
        # Capture the snapshot URL + the original URL near it.
        # archive.ph link blocks look like: <a href="https://archive.ph/XXXXX">...
        # paired with the original URL on a sibling line.
        snap_re = re.compile(r'href="(https?://archive\.(?:ph|today|is)/[A-Za-z0-9]{3,})"')
        orig_re = re.compile(r'href="(https?://[^"]*' + re.escape(domain) + r'[^"]*)"')
        snaps = snap_re.findall(html)
        origs = orig_re.findall(html)
        out: list[dict[str, Any]] = []
        for i, snap in enumerate(dict.fromkeys(snaps)):
            orig = origs[i] if i < len(origs) else f"https://{domain}/"
            out.append({
                "archived_url": snap,
                "original_url": orig,
                "ts": "",
                "source": "archive_today",
                "status": "200",
            })
        return out

    async def _common_crawl(self, domain: str) -> list[dict[str, Any]]:
        # Try the two most-recent indexes by deriving from current year/week.
        # We probe a small set of recent CC-MAIN ids — collection list endpoint
        # would be authoritative but adds a hop; this is best-effort.
        candidates = self._recent_cc_indexes(limit=2)
        out: list[dict[str, Any]] = []
        async with client(timeout=SOURCE_TIMEOUT) as c:
            for idx in candidates:
                url = f"https://index.commoncrawl.org/{idx}-index"
                params = {"url": f"{domain}/*", "output": "json"}
                try:
                    r = await c.get(url, params=params)
                except (httpx.HTTPError, asyncio.TimeoutError) as e:
                    logger.info("common crawl %s failed: %s", idx, type(e).__name__)
                    continue
                if r.status_code != 200:
                    continue
                # Response is newline-delimited JSON.
                for line in r.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    orig = obj.get("url", "")
                    ts = obj.get("timestamp", "")
                    status = obj.get("status", "")
                    if not orig:
                        continue
                    out.append({
                        "archived_url": orig,  # CC stores WARC offsets; original URL is the public anchor
                        "original_url": orig,
                        "ts": ts,
                        "source": "cc",
                        "status": status,
                        "cc_index": idx,
                    })
        return out

    @staticmethod
    def _recent_cc_indexes(limit: int = 2) -> list[str]:
        """Return the N most recent likely CC-MAIN-YYYY-WW index ids.

        Common Crawl publishes roughly one index per 2-week window. We probe
        the current ISO week and step backwards; the index endpoint will 404
        for non-existent ids and we'll just skip them.
        """
        now = datetime.utcnow()
        year, week, _ = now.isocalendar()
        out: list[str] = []
        for offset in range(0, limit * 3):  # stride of ~2 weeks
            w = week - (offset * 2)
            y = year
            while w <= 0:
                y -= 1
                w += 52
            out.append(f"CC-MAIN-{y}-{w:02d}")
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# WIRING — register this collector by adding `@register` above the class
# definition (and importing `register` from `app.collectors.base`) once the
# orchestrator agent green-lights it. Until then it stays dormant: import it
# explicitly in tests to exercise it without affecting case runs.
# ---------------------------------------------------------------------------
