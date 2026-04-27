"""CourtListener (Free Law Project) people search collector.

Queries https://www.courtlistener.com/api/rest/v3/people/ to surface US
court-related persons matching the subject's name. No API key required for
small volumes. 429/5xx → empty.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.courtlistener.com/api/rest/v3/people/"


@register
class CourtListenerCollector(Collector):
    name = "courtlistener"
    category = "legal"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "CourtListener: personas y casos del sistema judicial US."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        if not terms:
            return

        seen: set[str] = set()
        async with await get_client("gentle") as c:
            for term in terms:
                try:
                    r = await c.get(
                        SEARCH_URL,
                        params={"q": term},
                        timeout=20.0,
                    )
                except (httpx.HTTPError, OSError):
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    return
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                results = data.get("results") or []
                if not isinstance(results, list):
                    continue
                for p in results[:30]:
                    if not isinstance(p, dict):
                        continue
                    pid = p.get("id")
                    if pid is None or pid in seen:
                        continue
                    seen.add(pid)
                    name_full = (
                        " ".join(
                            part
                            for part in [
                                p.get("name_first"),
                                p.get("name_middle"),
                                p.get("name_last"),
                            ]
                            if part
                        )
                        or p.get("name_full")
                        or ""
                    )
                    url = p.get("absolute_url") or ""
                    if url and not url.startswith("http"):
                        url = f"https://www.courtlistener.com{url}"
                    yield Finding(
                        collector=self.name,
                        category="legal",
                        entity_type="Person",
                        title=f"CourtListener: {name_full}"[:200],
                        url=url or None,
                        confidence=0.7,
                        payload={
                            "kind": "court_person",
                            "evidence": {
                                "name": name_full,
                                "date_dob": p.get("date_dob"),
                                "date_dod": p.get("date_dod"),
                                "positions": p.get("positions"),
                                "political_affiliations": p.get(
                                    "political_affiliations"
                                ),
                                "source": "courtlistener",
                            },
                            "name_queried": term,
                        },
                    )
