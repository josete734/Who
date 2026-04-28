"""MX / DNS lookup for email domain.

Emits findings for:
  - disposable domain warning
  - MX records + provider hint
  - reverse PTR for any IP previously seen in the case (best-effort)
  - SPF, DMARC and DKIM (default selector) TXT records as ``dns_record`` entities
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput

# Minimal embedded list of common disposable email domains.
DISPOSABLE = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.net",
    "yopmail.com", "trashmail.com", "getnada.com", "dispostable.com",
    "maildrop.cc", "sharklasers.com", "throwawaymail.com", "mail-temp.com",
    "tempmail.com", "mohmal.com", "tempail.com", "temp-mail.org", "inbox.lv",
}


@register
class DnsMxCollector(Collector):
    name = "dns_mx"
    category = "email"
    needs = ("email",)
    timeout_seconds = 10

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.email:
            return
        try:
            local, domain = input.email.split("@", 1)
        except ValueError:
            return
        domain = domain.lower().strip()

        if domain in DISPOSABLE:
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="EmailWarning",
                title=f"Dominio desechable detectado: {domain}",
                url=None,
                confidence=1.0,
                payload={"domain": domain, "disposable": True},
            )

        try:
            import dns.asyncresolver  # type: ignore
            import dns.exception  # type: ignore
            import dns.reversename  # type: ignore
        except ImportError:
            return

        resolver = dns.asyncresolver.Resolver()
        resolver.timeout = 5.0
        resolver.lifetime = 5.0

        # MX
        mx_hosts: list[str] = []
        try:
            ans = await resolver.resolve(domain, "MX")
            mx_hosts = [str(r.exchange).rstrip(".") for r in ans]
        except Exception:
            mx_hosts = []

        if mx_hosts:
            provider = _detect_provider(mx_hosts)
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="EmailMX",
                title=f"MX de {domain}: {provider or mx_hosts[0]}",
                url=None,
                confidence=0.95,
                payload={"domain": domain, "mx_hosts": mx_hosts, "provider": provider},
            )
        else:
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="EmailWarning",
                title=f"Sin registros MX: {domain}",
                url=None,
                confidence=0.9,
                payload={"domain": domain, "mx_hosts": []},
            )

        # SPF, DMARC, DKIM (default selector) — emitted as dns_record entities.
        targets = [
            ("spf", domain, "v=spf1"),
            ("dmarc", f"_dmarc.{domain}", "v=DMARC1"),
            ("dkim", f"default._domainkey.{domain}", None),
        ]
        for kind, tgt, prefix in targets:
            try:
                ans = await resolver.resolve(tgt, "TXT")
            except Exception:
                continue
            for r in ans:
                try:
                    val = b"".join(r.strings).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                if prefix and not val.startswith(prefix):
                    continue
                yield Finding(
                    collector=self.name,
                    category="email",
                    entity_type="dns_record",
                    title=f"{kind.upper()} de {domain}",
                    url=None,
                    confidence=0.85,
                    payload={"kind": kind, "value": val, "domain": domain},
                )

        # Reverse PTR for any IPs already collected on this case.
        ips = _ips_from_input(input)
        for ip in ips:
            try:
                rev = dns.reversename.from_address(ip)
                ans = await resolver.resolve(rev, "PTR")
                ptrs = [str(r).rstrip(".") for r in ans]
            except Exception:
                continue
            if not ptrs:
                continue
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="dns_record",
                title=f"PTR {ip} -> {ptrs[0]}",
                url=None,
                confidence=0.8,
                payload={"kind": "ptr", "value": ptrs, "ip": ip, "domain": domain},
            )


def _ips_from_input(input: SearchInput) -> list[str]:
    """Best-effort extraction of IPs the orchestrator may attach to the input.

    The orchestrator may attach previously collected IPs via ``extra_context``
    (free-form). We also probe well-known optional attribute ``case_findings``
    if injected. Skipped silently when no IPs are available.
    """
    ips: list[str] = []
    raw = getattr(input, "case_findings", None) or []
    for f in raw:
        ip = (f.get("payload") or {}).get("ip") if isinstance(f, dict) else None
        if isinstance(ip, str):
            ips.append(ip)
    # Also scan extra_context for IPv4 strings.
    if input.extra_context:
        import re
        ips.extend(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", input.extra_context))
    # dedup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for ip in ips:
        if ip not in seen:
            seen.add(ip); out.append(ip)
    return out


def _detect_provider(mx_hosts: list[str]) -> str | None:
    s = " ".join(mx_hosts).lower()
    if "google" in s or "googlemail" in s:
        return "Google Workspace / Gmail"
    if "outlook" in s or "protection.outlook" in s or "office365" in s:
        return "Microsoft 365"
    if "proton" in s:
        return "Proton Mail"
    if "zoho" in s:
        return "Zoho Mail"
    if "yandex" in s:
        return "Yandex"
    if "fastmail" in s:
        return "Fastmail"
    if "yahoo" in s or "yahoodns" in s:
        return "Yahoo"
    if "icloud" in s or "apple" in s:
        return "iCloud"
    if "mailgun" in s or "amazonses" in s:
        return "Transactional provider"
    return None
