"""RDAP (modern WHOIS) collector (Wave 8).

RDAP is the IETF replacement for WHOIS — JSON over HTTPS, no key required,
served by every gTLD registry. ``rdap.org`` is the public meta-resolver
that figures out which registry holds the record and proxies the query.

Yields one ``DomainRDAP`` finding with the registrar, dates, nameservers,
and registrant info (where not redacted by GDPR).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_RDAP = "https://rdap.org/domain/"


def _entity_role(entity: dict, role: str) -> dict | None:
    roles = entity.get("roles") or []
    if role in roles:
        return entity
    return None


def _vcard_field(entity: dict, key: str) -> str | None:
    """Scan an RDAP vcardArray for a specific field (fn, email, etc.)."""
    arr = entity.get("vcardArray") or []
    if not isinstance(arr, list) or len(arr) < 2:
        return None
    for item in arr[1]:
        if isinstance(item, list) and len(item) >= 4 and item[0] == key:
            return str(item[3])
    return None


@register
class RDAPDomainCollector(Collector):
    name = "rdap"
    category = "infra"
    needs = ("domain",)
    timeout_seconds = 15
    description = "RDAP (modern WHOIS) lookup for the input domain — keyless."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        d = input.domain.strip().lower()
        if d.startswith("http"):
            d = d.split("://", 1)[1].split("/", 1)[0]
        url = _RDAP + d

        async with client(timeout=12) as c:
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return
        if not isinstance(data, dict):
            return

        # Registrar (entity with role "registrar")
        registrar: str | None = None
        registrant_name: str | None = None
        registrant_email: str | None = None
        for entity in data.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            if _entity_role(entity, "registrar"):
                registrar = _vcard_field(entity, "fn") or entity.get("handle")
            if _entity_role(entity, "registrant"):
                registrant_name = _vcard_field(entity, "fn")
                registrant_email = _vcard_field(entity, "email")

        # Events (registration, expiration, last-changed)
        events = {
            (e.get("eventAction") or ""): e.get("eventDate")
            for e in (data.get("events") or [])
            if isinstance(e, dict)
        }

        # Nameservers
        nameservers = []
        for ns in data.get("nameservers") or []:
            if isinstance(ns, dict):
                ldh = ns.get("ldhName")
                if ldh:
                    nameservers.append(ldh.lower())

        # Status flags (clientHold, pendingDelete, etc.)
        statuses = list(data.get("status") or [])

        yield Finding(
            collector=self.name,
            category="infra",
            entity_type="DomainRDAP",
            title=f"RDAP {d}: {registrar or 'unknown registrar'}",
            url=f"https://rdap.org/domain/{d}",
            confidence=0.95,
            payload={
                "domain": d,
                "registrar": registrar,
                "registrant_name": registrant_name,
                "registrant_email": registrant_email,
                "nameservers": nameservers,
                "registered": events.get("registration"),
                "expires": events.get("expiration"),
                "updated": events.get("last changed") or events.get("last update of RDAP database"),
                "statuses": statuses,
            },
        )
