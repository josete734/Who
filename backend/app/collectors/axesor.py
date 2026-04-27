"""Axesor.es collector — Spanish company directory.

Scrapes https://www.axesor.es search results for company names matching the
subject. Public HTML, no API key. 429/5xx → empty.
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

SEARCH_URL = "https://www.axesor.es/Informes-Empresas/b%C3%BAsqueda?q={q}"
_RESULT_RE = re.compile(
    r'<a[^>]+href="(https?://www\.axesor\.es/Informes-Empresas/[^"]+)"[^>]*>([^<]{3,200})</a>',
    re.IGNORECASE,
)
_NIF_RE = re.compile(r"\b([A-HJ-NP-SUVW]\d{7}[0-9A-J]|\d{8}[A-HJ-NP-TV-Z])\b")
_ADMIN_RE = re.compile(
    r"(?:Administrador(?:es)?|Apoderad[oa]s?)[^:]*:\s*([^<\n]+)",
    re.IGNORECASE,
)


@register
class AxesorCollector(Collector):
    name = "axesor"
    category = "registry"
    needs = ("full_name",)
    timeout_seconds = 20
    description = "Axesor.es: empresas, NIFs y administradores (ES)."

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
                for profile_url, label in _RESULT_RE.findall(html)[:20]:
                    if profile_url in seen:
                        continue
                    seen.add(profile_url)
                    nif = ""
                    admins: list[str] = []
                    try:
                        pr = await c.get(profile_url, timeout=20.0)
                    except (httpx.HTTPError, OSError):
                        pr = None
                    if pr is not None and pr.status_code == 200:
                        m = _NIF_RE.search(pr.text or "")
                        if m:
                            nif = m.group(1)
                        admins = [
                            a.strip()[:120]
                            for a in _ADMIN_RE.findall(pr.text or "")[:5]
                        ]
                    yield Finding(
                        collector=self.name,
                        category="registry",
                        entity_type="Company",
                        title=f"Axesor: {label.strip()}"[:200],
                        url=profile_url,
                        confidence=0.7,
                        payload={
                            "kind": "company_record",
                            "evidence": {
                                "name": label.strip(),
                                "nif": nif,
                                "administrators": admins,
                                "source": "axesor",
                            },
                            "name_queried": term,
                        },
                    )
