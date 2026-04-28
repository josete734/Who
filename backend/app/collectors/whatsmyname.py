"""WhatsMyName collector: tests username across the 1700+ sites in WMN's
`wmn-data.json`. Replaces/supplements `sherlock` and `maigret`.

Loads the upstream data file on first run (cached 24h via `app.cache`),
then probes each site concurrently using the shared netfetch client.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.cache import with_cache
from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

WMN_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
WMN_TTL = 24 * 3600
REQUEST_TIMEOUT = 8.0
CONCURRENCY = 40


@with_cache("whatsmyname", WMN_TTL)
async def _fetch_wmn_data(query: dict) -> dict[str, Any]:  # noqa: ARG001
    """Download wmn-data.json (cached 24h). `query` is part of the cache key."""
    async with await get_client("default") as c:
        r = await c.get(WMN_URL, timeout=30.0)
        r.raise_for_status()
        return r.json()


def _matches(site: dict, status: int, body: str) -> bool:
    e_code = site.get("e_code")
    if e_code is not None and int(e_code) != int(status):
        return False
    e_string = site.get("e_string")
    if e_string:
        if e_string.lower() not in body.lower():
            return False
    # m_string / m_code: explicit "missing" markers — if matched, NOT a hit.
    m_code = site.get("m_code")
    if m_code is not None and int(m_code) == int(status):
        # status equals the explicit miss code → reject only if e_code didn't
        # already disambiguate. e_code took precedence above, so safe to reject.
        return False
    m_string = site.get("m_string")
    if m_string and m_string.lower() in body.lower():
        return False
    return True


@register
class WhatsMyNameCollector(Collector):
    name = "whatsmyname"
    category = "username"
    needs = ("username",)
    timeout_seconds = 120
    description = "WhatsMyName: probes username across 1700+ sites (wmn-data.json)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        username = input.username.lstrip("@")

        try:
            data = await _fetch_wmn_data({"v": "wmn-data"})
        except Exception:
            logger.warning("whatsmyname: failed to fetch wmn-data.json", exc_info=True)
            return

        sites = data.get("sites") or []
        if not sites:
            return

        sem = asyncio.Semaphore(CONCURRENCY)
        seen_urls: set[str] = set()

        async with await get_client("default") as client:

            async def probe(site: dict) -> Finding | None:
                uri = site.get("uri_check")
                if not uri or "{account}" not in uri:
                    return None
                url = uri.replace("{account}", username)
                async with sem:
                    try:
                        r = await client.get(url, timeout=REQUEST_TIMEOUT)
                    except (httpx.HTTPError, asyncio.TimeoutError, OSError):
                        return None
                    except Exception:  # pragma: no cover - defensive
                        return None
                try:
                    body = r.text
                except Exception:  # pragma: no cover
                    body = ""
                if not _matches(site, r.status_code, body):
                    return None
                resolved = str(r.url) or url
                return Finding(
                    collector=self.name,
                    category="account",
                    entity_type="account",
                    title=str(site.get("name") or "site"),
                    url=resolved,
                    confidence=0.7,
                    payload={
                        "platform": site.get("name"),
                        "category": site.get("cat"),
                        "username": username,
                    },
                )

            tasks = [asyncio.create_task(probe(s)) for s in sites if isinstance(s, dict)]
            for coro in asyncio.as_completed(tasks):
                try:
                    finding = await coro
                except Exception:
                    continue
                if finding is None:
                    continue
                key = (finding.url or "").lower()
                if key and key in seen_urls:
                    continue
                if key:
                    seen_urls.add(key)
                yield finding
