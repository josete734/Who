"""ORCID collector: search academic researchers by name or email."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class OrcidCollector(Collector):
    name = "orcid"
    category = "academic"
    needs = ("full_name", "birth_name", "aliases", "email")
    timeout_seconds = 30
    description = "ORCID public API: researchers by name or email."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        queries: list[str] = []
        if input.email:
            queries.append(f'email:"{input.email}"')
        for name in input.name_variants():
            parts = name.split()
            if len(parts) >= 2:
                queries.append(f'given-names:"{parts[0]}" AND family-name:"{parts[-1]}"')
            else:
                queries.append(f'given-names:"{name}"')
        if not queries:
            return

        async with client(timeout=20, headers={"Accept": "application/json"}) as c:
            for q in queries:
                try:
                    r = await c.get(
                        "https://pub.orcid.org/v3.0/expanded-search/",
                        params={"q": q, "rows": 10},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                data = r.json() or {}
                results = data.get("expanded-result") or []
                for hit in results[:10]:
                    orcid = hit.get("orcid-id")
                    if not orcid:
                        continue
                    display = f"{hit.get('given-names', '')} {hit.get('family-names', '')}".strip()
                    yield Finding(
                        collector=self.name,
                        category="academic",
                        entity_type="ORCIDProfile",
                        title=f"ORCID: {display or orcid}",
                        url=f"https://orcid.org/{orcid}",
                        confidence=0.85 if input.email else 0.55,
                        payload={
                            "orcid": orcid,
                            "given_names": hit.get("given-names"),
                            "family_names": hit.get("family-names"),
                            "institution": hit.get("institution-name"),
                            "email": hit.get("email"),
                        },
                    )
