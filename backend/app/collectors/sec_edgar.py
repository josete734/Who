"""SEC EDGAR full-text search collector.

Queries https://efts.sec.gov/LATEST/search-index for filings mentioning the
subject's name (verbatim quoted). Returns one finding per filing hit. 429/5xx
responses are silently ignored.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"


@register
class SecEdgarCollector(Collector):
    name = "sec_edgar"
    category = "registry"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "SEC EDGAR full-text filings search."

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
                        params={"q": f'"{term}"', "hits": "20"},
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
                hits = (data.get("hits") or {}).get("hits") or []
                if not isinstance(hits, list):
                    continue
                for h in hits:
                    if not isinstance(h, dict):
                        continue
                    src = h.get("_source") or {}
                    adsh = (src.get("adsh") or "").replace("-", "")
                    cik = ""
                    ciks = src.get("ciks") or []
                    if isinstance(ciks, list) and ciks:
                        cik = str(ciks[0]).lstrip("0") or "0"
                    form = src.get("form") or ""
                    file_date = src.get("file_date") or ""
                    display = src.get("display_names") or []
                    name_label = display[0] if display else ""
                    fid = h.get("_id") or adsh
                    if not fid or fid in seen:
                        continue
                    seen.add(fid)
                    primary = ""
                    files = src.get("file") or src.get("file_type") or ""
                    url = None
                    if cik and adsh:
                        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                    yield Finding(
                        collector=self.name,
                        category="registry",
                        entity_type="Filing",
                        title=f"SEC EDGAR {form}: {name_label} ({file_date})"[:200],
                        url=url,
                        confidence=0.7,
                        payload={
                            "kind": "sec_filing",
                            "evidence": {
                                "form": form,
                                "file_date": file_date,
                                "ciks": ciks,
                                "display_names": display,
                                "adsh": src.get("adsh"),
                                "primary_doc": primary or files,
                                "source": "sec_edgar",
                            },
                            "name_queried": term,
                        },
                    )
