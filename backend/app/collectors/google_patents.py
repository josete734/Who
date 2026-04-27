"""Google Patents inventor scraper.

Fetches https://patents.google.com/?inventor={name} and extracts patent
records embedded in the HTML. Best-effort regex parser — Google's markup
changes occasionally. 429/5xx → empty.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://patents.google.com/?inventor={q}"
_RESULT_RE = re.compile(
    r'<search-result-item[^>]*data-result="/?patent/([A-Z0-9]+)/?[^"]*"[^>]*>'
    r'.*?<h3[^>]*>([^<]+)</h3>'
    r'.*?<h4[^>]*data-result="filing-date"[^>]*>([^<]*)</h4>',
    re.IGNORECASE | re.DOTALL,
)
_FALLBACK_RE = re.compile(
    r'href="/patent/([A-Z0-9]+)[^"]*"[^>]*>([^<]{5,200})</a>',
    re.IGNORECASE,
)


@register
class GooglePatentsCollector(Collector):
    name = "google_patents"
    category = "academic"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "Google Patents: patentes US/global por inventor."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        if not terms:
            return

        seen: set[str] = set()
        async with await get_client("gentle") as c:
            for term in terms:
                url = SEARCH_URL.format(q=quote_plus(term))
                try:
                    r = await c.get(url, timeout=20.0)
                except (httpx.HTTPError, OSError):
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    return
                if r.status_code != 200:
                    continue
                html = r.text or ""

                matches = list(_RESULT_RE.findall(html))
                if not matches:
                    matches = [
                        (pid, title, "")
                        for pid, title in _FALLBACK_RE.findall(html)[:30]
                    ]

                for pid, title, filing in matches[:30]:
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    yield Finding(
                        collector=self.name,
                        category="academic",
                        entity_type="Patent",
                        title=f"Google Patents: {title.strip()}"[:200],
                        url=f"https://patents.google.com/patent/{pid}/",
                        confidence=0.7,
                        payload={
                            "kind": "patent",
                            "evidence": {
                                "publication_number": pid,
                                "title": title.strip(),
                                "filing_date": (filing or "").strip(),
                                "source": "google_patents",
                            },
                            "name_queried": term,
                        },
                    )
