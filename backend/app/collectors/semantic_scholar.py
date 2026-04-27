"""Semantic Scholar author search collector.

Calls api.semanticscholar.org graph/v1/author/search to list academic authors
matching the subject's name, with affiliations and paper counts. No API key
required for low volumes; supplying SEMANTIC_SCHOLAR_API_KEY raises limits.
429/5xx → empty.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.semanticscholar.org/graph/v1/author/search"


@register
class SemanticScholarCollector(Collector):
    name = "semantic_scholar"
    category = "academic"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "Semantic Scholar: autores académicos y publicaciones."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        if not terms:
            return

        api_key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
        headers = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key

        seen: set[str] = set()
        async with await get_client("gentle") as c:
            for term in terms:
                try:
                    r = await c.get(
                        SEARCH_URL,
                        params={
                            "query": term,
                            "fields": "name,affiliations,paperCount",
                            "limit": "20",
                        },
                        headers=headers,
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
                authors = data.get("data") or []
                if not isinstance(authors, list):
                    continue
                for a in authors:
                    if not isinstance(a, dict):
                        continue
                    aid = a.get("authorId") or ""
                    if not aid or aid in seen:
                        continue
                    seen.add(aid)
                    name = a.get("name") or ""
                    affs = a.get("affiliations") or []
                    paper_count = a.get("paperCount")
                    aff_label = ", ".join(affs[:2]) if isinstance(affs, list) else ""
                    title = f"Semantic Scholar: {name}"
                    if aff_label:
                        title += f" ({aff_label})"
                    yield Finding(
                        collector=self.name,
                        category="academic",
                        entity_type="Author",
                        title=title[:200],
                        url=f"https://www.semanticscholar.org/author/{aid}",
                        confidence=0.7,
                        payload={
                            "kind": "academic_author",
                            "evidence": {
                                "name": name,
                                "author_id": aid,
                                "affiliations": affs,
                                "paper_count": paper_count,
                                "source": "semantic_scholar",
                            },
                            "name_queried": term,
                            "authenticated": bool(api_key),
                        },
                    )
