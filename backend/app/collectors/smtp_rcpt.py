"""SMTP RCPT TO probe.

Given an email, resolves MX with dnspython, opens an SMTP session via aiosmtplib,
issues EHLO, MAIL FROM:<probe@example.org>, RCPT TO:<email>. The session is
aborted before DATA — no message is ever sent.

Verdicts:
  * 250  -> ``deliverable``
  * 550  -> ``not_deliverable``
  * any other / TCP error -> ``unknown``

Skips well-known providers (Gmail, Outlook, Yahoo, Hotmail, iCloud) which
either greylist anonymous probes or always answer 250 regardless of mailbox
existence — the result is meaningless for them.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput

SKIP_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.es", "ymail.com",
    "icloud.com", "me.com", "mac.com",
}

PROBE_FROM = "probe@example.org"


@register
class SmtpRcptCollector(Collector):
    name = "smtp_rcpt"
    category = "email"
    needs = ("email",)
    timeout_seconds = 20
    description = "SMTP RCPT TO deliverability probe (no DATA sent)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        emails = input.emails()
        if not emails:
            return

        try:
            import dns.asyncresolver  # type: ignore
        except ImportError:
            return
        try:
            import aiosmtplib  # type: ignore
        except ImportError:
            return

        for raw_email in emails:
            email = raw_email.strip().lower()
            try:
                _, domain = email.split("@", 1)
            except ValueError:
                continue
            if domain in SKIP_DOMAINS:
                continue

            resolver = dns.asyncresolver.Resolver()
            resolver.timeout = 5.0
            resolver.lifetime = 5.0
            try:
                ans = await resolver.resolve(domain, "MX")
                mx_hosts = sorted(
                    [(int(r.preference), str(r.exchange).rstrip(".")) for r in ans],
                    key=lambda x: x[0],
                )
            except Exception:
                continue
            if not mx_hosts:
                continue

            host = mx_hosts[0][1]

            verdict = "unknown"
            rcpt_code: int | None = None
            rcpt_msg: str = ""

            try:
                smtp = aiosmtplib.SMTP(hostname=host, port=25, timeout=15)
                await smtp.connect()
                try:
                    await smtp.ehlo("example.org")
                    await smtp.mail(PROBE_FROM)
                    code, msg = await smtp.rcpt(email)
                    rcpt_code = int(code)
                    rcpt_msg = (msg or "").strip()[:200] if isinstance(msg, str) else str(msg)[:200]
                    if rcpt_code == 250 or rcpt_code == 251:
                        verdict = "deliverable"
                    elif rcpt_code == 550 or rcpt_code == 551 or rcpt_code == 553:
                        verdict = "not_deliverable"
                    else:
                        verdict = "unknown"
                finally:
                    try:
                        await smtp.quit()
                    except Exception:
                        try:
                            smtp.close()
                        except Exception:
                            pass
            except Exception as exc:  # TCP/SMTP failure
                yield Finding(
                    collector=self.name,
                    category="email",
                    entity_type="EmailDeliverability",
                    title=f"SMTP probe inconclusivo ({domain})",
                    url=None,
                    confidence=0.3,
                    payload={
                        "email": email,
                        "domain": domain,
                        "mx": host,
                        "verdict": "unknown",
                        "error": str(exc)[:200],
                    },
                )
                continue

            confidence = {"deliverable": 0.85, "not_deliverable": 0.85, "unknown": 0.4}[verdict]
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="EmailDeliverability",
                title=f"SMTP {verdict} ({email})",
                url=None,
                confidence=confidence,
                payload={
                    "email": email,
                    "domain": domain,
                    "mx": host,
                    "verdict": verdict,
                    "rcpt_code": rcpt_code,
                    "rcpt_message": rcpt_msg,
                },
            )
