"""EU corporate registries via OpenCorporates.

Searches officer records across EU jurisdictions (ES, GB, FR, DE, IT, PT) using
the OpenCorporates public API. If ``OPENCORPORATES_API_KEY`` is set in the
environment, it is sent as ``api_token`` for higher rate limits; otherwise the
free tier is hit with conservative pacing.

Officer matches are emitted as findings tagged ``kind=officer_record`` with
the source jurisdiction in the evidence payload.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client, jitter_sleep
from app.schemas import SearchInput

OPENCORPORATES_OFFICERS_URL = "https://api.opencorporates.com/v0.4/officers/search"
EU_JURISDICTIONS = "es,gb,fr,de,it,pt"


def _api_key() -> str | None:
    """Read OpenCorporates API key from settings/env (optional)."""
    key = os.getenv("OPENCORPORATES_API_KEY", "").strip()
    if key:
        return key
    try:
        from app.config import get_settings

        s = get_settings()
        val = getattr(s, "opencorporates_api_key", "") or ""
        return val.strip() or None
    except Exception:
        return None


@register
class EURegistriesCollector(Collector):
    name = "eu_registries"
    category = "eu_official"
    needs = ("full_name", "birth_name", "aliases", "company_name", "domain")
    timeout_seconds = 45
    description = "OpenCorporates: officer records across EU registries (ES/GB/FR/DE/IT/PT)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms: list[str] = list(input.name_variants())
        company = getattr(input, "company_name", None)
        if company:
            terms.append(company)
        domain = input.domain
        if domain:
            base = domain.split(".")[0]
            if base and base.lower() not in {t.lower() for t in terms}:
                terms.append(base)

        if not terms:
            return

        api_key = _api_key()
        has_key = bool(api_key)
        seen: set[str] = set()

        async with client(timeout=30) as c:
            for term in terms:
                params = {
                    "q": term,
                    "jurisdiction_code": EU_JURISDICTIONS,
                    "per_page": "30",
                }
                if api_key:
                    params["api_token"] = api_key
                qs = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
                url = f"{OPENCORPORATES_OFFICERS_URL}?{qs}"

                if not has_key:
                    # Free tier: pace requests to avoid 403/429.
                    await jitter_sleep(1.0, 2.0)

                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue

                officers = (
                    data.get("results", {}).get("officers", [])
                    if isinstance(data, dict)
                    else []
                )
                for entry in officers:
                    officer = entry.get("officer") if isinstance(entry, dict) else None
                    if not isinstance(officer, dict):
                        continue
                    name = (officer.get("name") or "").strip()
                    if not name:
                        continue
                    op_url = officer.get("opencorporates_url") or ""
                    dedup_key = op_url or f"{name}|{officer.get('jurisdiction_code', '')}|{officer.get('company', {}).get('name', '')}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    jurisdiction = officer.get("jurisdiction_code") or ""
                    company_obj = officer.get("company") or {}
                    company_name = company_obj.get("name") or ""
                    position = officer.get("position") or ""

                    title_parts = [name]
                    if position:
                        title_parts.append(position)
                    if company_name:
                        title_parts.append(f"@ {company_name}")
                    if jurisdiction:
                        title_parts.append(f"[{jurisdiction.upper()}]")
                    title = " ".join(title_parts)[:200]

                    yield Finding(
                        collector=self.name,
                        category="eu_official",
                        entity_type="OfficerRecord",
                        title=title,
                        url=op_url or None,
                        confidence=0.7,
                        payload={
                            "kind": "officer_record",
                            "evidence": {
                                "jurisdiction": jurisdiction,
                                "name": name,
                                "position": position,
                                "company_name": company_name,
                                "company_number": company_obj.get("company_number"),
                                "start_date": officer.get("start_date"),
                                "end_date": officer.get("end_date"),
                                "source": "opencorporates",
                            },
                            "name_queried": term,
                            "authenticated": has_key,
                        },
                    )
