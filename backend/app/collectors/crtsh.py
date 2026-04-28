"""crt.sh — Certificate Transparency lookup.

If a domain is known, we list recent certificates (subdomains, emails).
If only an email is known, we search for CNs/SANs that include it.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class CrtShDomainCollector(Collector):
    name = "crtsh_domain"
    category = "domain"
    needs = ("domain",)
    timeout_seconds = 30
    description = "crt.sh: Certificate Transparency by domain (subdomain discovery)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        q = f"%25.{input.domain.strip('.')}"
        async with client(timeout=25) as c:
            try:
                r = await c.get("https://crt.sh/", params={"q": q, "output": "json"})
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                return
        seen: set[str] = set()
        for row in data[:1000]:
            name_value = row.get("name_value") or ""
            for name in name_value.split("\n"):
                name = name.strip().lower()
                if not name or name in seen:
                    continue
                seen.add(name)
                yield Finding(
                    collector=self.name,
                    category="domain",
                    entity_type="Subdomain",
                    title=name,
                    url=f"https://crt.sh/?q={name}",
                    confidence=0.9,
                    payload={"issuer": row.get("issuer_name"), "not_before": row.get("not_before")},
                )


@register
class CrtShEmailCollector(Collector):
    name = "crtsh_email"
    category = "email"
    needs = ("email",)
    timeout_seconds = 30
    description = "crt.sh: search certificates that mention the email address."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.email:
            return
        async with client(timeout=25) as c:
            try:
                r = await c.get("https://crt.sh/", params={"q": input.email, "output": "json"})
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                return
        for row in data[:200]:
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="CertificateMention",
                title=f"Certificado con {input.email}: {row.get('common_name', '')}",
                url=f"https://crt.sh/?id={row.get('id')}",
                confidence=0.75,
                payload={
                    "common_name": row.get("common_name"),
                    "issuer": row.get("issuer_name"),
                    "not_before": row.get("not_before"),
                    "not_after": row.get("not_after"),
                    "name_value": row.get("name_value"),
                },
            )
