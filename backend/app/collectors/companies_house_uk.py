"""Companies House UK collector — public search endpoint.

Hits https://api.company-information.service.gov.uk/search/companies which
exposes basic company metadata without an API key for moderate volumes. We
follow up with /company/{number}/officers when available to surface directors.
429/5xx → empty.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"
OFFICERS_URL = "https://api.company-information.service.gov.uk/company/{num}/officers"


@register
class CompaniesHouseUKCollector(Collector):
    name = "companies_house_uk"
    category = "registry"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "Companies House (UK): empresas y directores."

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
                        params={"q": term, "items_per_page": "20"},
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
                items = data.get("items") or []
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    num = (it.get("company_number") or "").strip()
                    title = (it.get("title") or "").strip()
                    if not num or num in seen:
                        continue
                    seen.add(num)
                    company_url = (
                        f"https://find-and-update.company-information.service.gov.uk/company/{num}"
                    )

                    directors: list[dict] = []
                    try:
                        ofr = await c.get(
                            OFFICERS_URL.format(num=num), timeout=20.0
                        )
                    except (httpx.HTTPError, OSError):
                        ofr = None
                    if ofr is not None and ofr.status_code == 200:
                        try:
                            od = ofr.json()
                        except ValueError:
                            od = {}
                        for off in (od.get("items") or [])[:20]:
                            if not isinstance(off, dict):
                                continue
                            directors.append(
                                {
                                    "name": off.get("name"),
                                    "officer_role": off.get("officer_role"),
                                    "appointed_on": off.get("appointed_on"),
                                    "resigned_on": off.get("resigned_on"),
                                    "nationality": off.get("nationality"),
                                }
                            )

                    yield Finding(
                        collector=self.name,
                        category="registry",
                        entity_type="Company",
                        title=f"Companies House UK: {title}"[:200],
                        url=company_url,
                        confidence=0.7,
                        payload={
                            "kind": "company_record",
                            "evidence": {
                                "name": title,
                                "company_number": num,
                                "company_status": it.get("company_status"),
                                "address": it.get("address_snippet"),
                                "date_of_creation": it.get("date_of_creation"),
                                "directors": directors,
                                "source": "companies_house_uk",
                            },
                            "name_queried": term,
                        },
                    )
