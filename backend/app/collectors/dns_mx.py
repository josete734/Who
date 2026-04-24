"""MX / DNS lookup for email domain — disposable check, catch-all hint."""
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
        assert input.email
        try:
            local, domain = input.email.split("@", 1)
        except ValueError:
            return
        domain = domain.lower().strip()

        # Disposable check (no network)
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
        except ImportError:
            return

        resolver = dns.asyncresolver.Resolver()
        resolver.timeout = 5.0
        resolver.lifetime = 5.0

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

        # TXT / SPF / DMARC
        for kind, tgt in [("SPF", domain), ("DMARC", f"_dmarc.{domain}")]:
            try:
                ans = await resolver.resolve(tgt, "TXT")
                for r in ans:
                    val = b"".join(r.strings).decode("utf-8", errors="ignore")
                    if (kind == "SPF" and val.startswith("v=spf1")) or (kind == "DMARC" and val.startswith("v=DMARC1")):
                        yield Finding(
                            collector=self.name,
                            category="email",
                            entity_type=f"Email{kind}",
                            title=f"{kind} de {domain}",
                            url=None,
                            confidence=0.85,
                            payload={"record": val},
                        )
            except Exception:
                pass


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
