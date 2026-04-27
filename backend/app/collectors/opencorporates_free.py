"""OpenCorporates free-tier company search.

Complements ``eu_registries`` (which targets officers across EU jurisdictions)
by searching companies globally via /v0.4/companies/search. No API key — free
tier only. 429/5xx → empty.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.opencorporates.com/v0.4/companies/search"


@register
class OpenCorporatesFreeCollector(Collector):
    name = "opencorporates_free"
    category = "registry"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "OpenCorporates (free): empresas globales por nombre."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        company = getattr(input, "company_name", None)
        if company:
            terms.append(company)
        if not terms:
            return

        seen: set[str] = set()
        async with await get_client("gentle") as c:
            for term in terms:
                try:
                    r = await c.get(
                        SEARCH_URL,
                        params={"q": term, "per_page": "30"},
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
                companies = (data.get("results") or {}).get("companies") or []
                if not isinstance(companies, list):
                    continue
                for entry in companies:
                    if not isinstance(entry, dict):
                        continue
                    co = entry.get("company") or {}
                    if not isinstance(co, dict):
                        continue
                    op_url = co.get("opencorporates_url") or ""
                    key = op_url or f"{co.get('name')}|{co.get('jurisdiction_code')}|{co.get('company_number')}"
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    name = co.get("name") or ""
                    juris = co.get("jurisdiction_code") or ""
                    title = name
                    if juris:
                        title += f" [{juris.upper()}]"
                    yield Finding(
                        collector=self.name,
                        category="registry",
                        entity_type="Company",
                        title=f"OpenCorporates: {title}"[:200],
                        url=op_url or None,
                        confidence=0.7,
                        payload={
                            "kind": "company_record",
                            "evidence": {
                                "name": name,
                                "jurisdiction": juris,
                                "company_number": co.get("company_number"),
                                "company_type": co.get("company_type"),
                                "incorporation_date": co.get("incorporation_date"),
                                "dissolution_date": co.get("dissolution_date"),
                                "current_status": co.get("current_status"),
                                "source": "opencorporates",
                            },
                            "name_queried": term,
                            "authenticated": False,
                        },
                    )
