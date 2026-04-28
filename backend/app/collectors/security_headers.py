"""Tech-stack fingerprint via HTTP response headers + security.txt (Wave 8).

A single HEAD on ``https://{domain}`` exposes a wealth of free signal:

* ``Server`` / ``X-Powered-By`` / ``X-Generator`` / ``Via`` — backend tech
* ``CF-Ray`` (Cloudflare), ``X-Vercel-Id`` (Vercel), ``x-amzn-RequestId``
  (AWS API Gateway) — hosting infrastructure
* ``Strict-Transport-Security`` — HSTS posture
* ``Content-Security-Policy`` — third-party domains pulled in (analytics,
  ads, CDNs)

Plus ``GET /.well-known/security.txt`` to surface bug-bounty contacts and
PGP keys for the security team.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_FINGERPRINT_HEADERS: tuple[str, ...] = (
    "Server",
    "X-Powered-By",
    "X-Generator",
    "X-AspNet-Version",
    "X-Drupal-Cache",
    "X-Wix-Request-Id",
    "X-Shopify-Stage",
    "X-Vercel-Id",
    "X-Vercel-Cache",
    "x-amzn-RequestId",
    "x-amz-cf-id",
    "CF-Ray",
    "CF-Cache-Status",
    "X-GitHub-Request-Id",
    "X-Akamai-Edgescape",
    "Via",
    "Strict-Transport-Security",
)

_CSP_THIRD_PARTY_RE = re.compile(r"https?://[^\s;'\"]+", re.IGNORECASE)


def _csp_third_parties(csp: str) -> list[str]:
    if not csp:
        return []
    seen: set[str] = set()
    for url in _CSP_THIRD_PARTY_RE.findall(csp):
        # Strip path, keep host.
        host = url.split("://", 1)[1].split("/", 1)[0].lower()
        if host:
            seen.add(host)
    return sorted(seen)


@register
class SecurityHeadersCollector(Collector):
    name = "security_headers"
    category = "infra"
    needs = ("domain",)
    timeout_seconds = 12
    description = "Passive tech fingerprint via HTTP response headers + security.txt."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.domain:
            return
        d = input.domain.strip().lower()
        if d.startswith("http"):
            d = d.split("://", 1)[1].split("/", 1)[0]
        site = f"https://{d}/"

        async with client(timeout=10) as c:
            # 1) HEAD to the homepage (fall back to GET on 405).
            try:
                r = await c.head(site, follow_redirects=True)
                if r.status_code == 405:
                    r = await c.get(site)
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code < 500:
                fp: dict[str, str] = {}
                for h in _FINGERPRINT_HEADERS:
                    v = r.headers.get(h)
                    if v:
                        fp[h] = str(v)[:300]
                csp = r.headers.get("Content-Security-Policy", "")
                third_parties = _csp_third_parties(csp) if csp else []
                if fp or third_parties:
                    yield Finding(
                        collector=self.name,
                        category="infra",
                        entity_type="HTTPFingerprint",
                        title=f"Headers de {d}: {len(fp)} señal(es), {len(third_parties)} terceros (CSP)",
                        url=site,
                        confidence=0.8,
                        payload={
                            "domain": d,
                            "headers": fp,
                            "csp_third_parties": third_parties,
                            "status_code": int(r.status_code),
                        },
                    )

            # 2) /.well-known/security.txt (often contains PGP / bug bounty contacts).
            try:
                rs = await c.get(f"https://{d}/.well-known/security.txt")
            except httpx.HTTPError:
                rs = None
            if rs is not None and rs.status_code == 200 and rs.text:
                contacts: list[str] = []
                policy: str | None = None
                pgp: str | None = None
                for line in rs.text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("contact:"):
                        contacts.append(line.split(":", 1)[1].strip())
                    elif line.lower().startswith("policy:"):
                        policy = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("encryption:"):
                        pgp = line.split(":", 1)[1].strip()
                if contacts or policy or pgp:
                    yield Finding(
                        collector=self.name,
                        category="infra",
                        entity_type="SecurityTxt",
                        title=f"security.txt de {d}: {len(contacts)} contacto(s)",
                        url=f"https://{d}/.well-known/security.txt",
                        confidence=0.9,
                        payload={
                            "domain": d,
                            "contacts": contacts,
                            "policy": policy,
                            "encryption": pgp,
                        },
                    )
