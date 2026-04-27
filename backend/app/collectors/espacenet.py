"""Espacenet (EPO) inventor search collector.

Uses the public REST search at worldwide.espacenet.com to surface European
patents where the subject's name appears in the inventor field. 429/5xx →
empty.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://worldwide.espacenet.com/3.2/rest-services/search?q=in%3D%22{q}%22"
)


@register
class EspacenetCollector(Collector):
    name = "espacenet"
    category = "academic"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "Espacenet (EPO): patentes EU por inventor."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        if not terms:
            return

        seen: set[str] = set()
        async with await get_client("gentle") as c:
            for term in terms:
                url = SEARCH_URL.format(q=quote_plus(term))
                try:
                    r = await c.get(
                        url,
                        headers={"Accept": "application/json"},
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
                # Espacenet wraps results under several keys depending on version.
                results = []
                if isinstance(data, dict):
                    container = (
                        data.get("results")
                        or data.get("ops:world-patent-data")
                        or {}
                    )
                    if isinstance(container, dict):
                        results = (
                            container.get("publications")
                            or container.get("hits")
                            or container.get("ops:biblio-search", {}).get(
                                "ops:search-result", []
                            )
                            or []
                        )
                    elif isinstance(container, list):
                        results = container
                if not isinstance(results, list):
                    continue
                for entry in results[:30]:
                    if not isinstance(entry, dict):
                        continue
                    pub = (
                        entry.get("publication_number")
                        or entry.get("docNumber")
                        or entry.get("id")
                        or ""
                    )
                    if not pub or pub in seen:
                        continue
                    seen.add(pub)
                    title = (
                        entry.get("title")
                        or entry.get("invention_title")
                        or pub
                    )
                    pub_date = entry.get("publication_date") or entry.get(
                        "date"
                    )
                    inventors = entry.get("inventors") or entry.get(
                        "inventor"
                    ) or []
                    yield Finding(
                        collector=self.name,
                        category="academic",
                        entity_type="Patent",
                        title=f"Espacenet: {title}"[:200],
                        url=f"https://worldwide.espacenet.com/patent/search/publication/{pub}",
                        confidence=0.7,
                        payload={
                            "kind": "patent",
                            "evidence": {
                                "publication_number": pub,
                                "title": title,
                                "publication_date": pub_date,
                                "inventors": inventors,
                                "source": "espacenet",
                            },
                            "name_queried": term,
                        },
                    )
