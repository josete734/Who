"""ORCID collector: search academic researchers by name or email."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.collectors._relevance import context_from_input, score_relevance
from app.http_util import client
from app.schemas import SearchInput

logger = logging.getLogger("osint.collectors.orcid")


@register
class OrcidCollector(Collector):
    name = "orcid"
    category = "academic"
    needs = ("full_name", "birth_name", "aliases", "email")
    timeout_seconds = 30
    max_retries = 1
    description = "ORCID public API: researchers by name or email."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        queries: list[str] = []
        if input.email:
            queries.append(f'email:"{input.email}"')
        for name in input.name_variants():
            if not name:
                continue
            parts = [p for p in name.split() if p]
            if not parts:
                continue
            if len(parts) >= 2:
                queries.append(f'given-names:"{parts[0]}" AND family-name:"{parts[-1]}"')
            else:
                queries.append(f'given-names:"{parts[0]}"')
        if not queries:
            return

        ctx = context_from_input(input)
        emitted = 0
        max_total = 5
        async with client(timeout=20, headers={"Accept": "application/json"}) as c:
            for q in queries:
                if emitted >= max_total:
                    break
                try:
                    r = await c.get(
                        "https://pub.orcid.org/v3.0/expanded-search/",
                        params={"q": q, "rows": 10},
                    )
                except httpx.HTTPError as e:
                    logger.info(
                        "orcid request failed",
                        extra={"collector": self.name, "query": q, "error": type(e).__name__},
                    )
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                # Defensive: ORCID has been observed to return 200 with a body
                # of ``null``, an empty string, or ``{"expanded-result": null}``.
                # Any of these would NPE the previous version on the dict-get
                # chain or on iterating ``hit``.
                if not isinstance(data, dict):
                    continue
                results = data.get("expanded-result")
                if not isinstance(results, list):
                    continue
                for hit in results[:10]:
                    if emitted >= max_total:
                        break
                    if not isinstance(hit, dict):
                        continue
                    orcid = hit.get("orcid-id")
                    if not orcid:
                        continue
                    given = hit.get("given-names") or ""
                    family = hit.get("family-names") or ""
                    display = f"{given} {family}".strip()
                    institution = hit.get("institution-name")
                    # institution may be a list per ORCID schema — normalise.
                    if isinstance(institution, list):
                        institution = ", ".join(str(x) for x in institution if x)
                    email_val = hit.get("email")
                    if isinstance(email_val, list):
                        email_val = email_val[0] if email_val else None
                    text_blob = " ".join(
                        str(x) for x in (display, institution, email_val, orcid) if x
                    )
                    rel = score_relevance(text_blob, ctx)
                    if rel >= 0.5:
                        confidence = 0.85
                        role = "confirmed"
                    else:
                        confidence = 0.25
                        role = "candidate_homonym"
                    yield Finding(
                        collector=self.name,
                        category="academic",
                        entity_type="ORCIDProfile",
                        title=f"ORCID: {display or orcid}",
                        url=f"https://orcid.org/{orcid}",
                        confidence=confidence,
                        payload={
                            "orcid": orcid,
                            "given_names": given or None,
                            "family_names": family or None,
                            "institution": institution,
                            "email": email_val,
                            "role": role,
                            "relevance": rel,
                        },
                    )
                    emitted += 1
