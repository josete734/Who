"""wa.me WhatsApp existence check.

Hitting https://wa.me/<num> returns different page contents depending on whether
the number is in WhatsApp or not. This is passive (no message sent).

Additionally we:
  - issue a HEAD against the canonical URL to capture redirect target,
  - check that ``https://api.whatsapp.com/send/?phone=<num>`` redirects to wa.me
    (positive signal),
  - record the inferred ISO country code from the prefix.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import phonenumbers
from phonenumbers import region_code_for_number

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class WaMeCollector(Collector):
    name = "wa_me"
    category = "phone"
    needs = ("phone",)
    timeout_seconds = 15
    description = "WhatsApp existence check via wa.me (passive)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.phone
        try:
            num = phonenumbers.parse(input.phone, None if input.phone.startswith("+") else "ES")
        except phonenumbers.NumberParseException:
            return
        digits = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164).lstrip("+")
        country_code = region_code_for_number(num)
        country_prefix = num.country_code
        url = f"https://wa.me/{digits}"
        api_url = f"https://api.whatsapp.com/send/?phone={digits}"

        canonical_redirect: str | None = None
        api_redirects_to_wa = False

        async with client(timeout=12) as c:
            # HEAD canonical wa.me — capture final redirect URL.
            try:
                head = await c.head(url)
                canonical_redirect = str(head.url)
            except httpx.HTTPError:
                canonical_redirect = None

            # api.whatsapp.com → wa.me signal.
            try:
                api_head = await c.head(api_url)
                final_host = (api_head.url.host or "").lower()
                if "wa.me" in final_host or final_host.endswith("whatsapp.com"):
                    # Only consider it a positive signal if it bounced to wa.me
                    api_redirects_to_wa = "wa.me" in final_host
            except httpx.HTTPError:
                api_redirects_to_wa = False

            try:
                r = await c.get(url)
            except httpx.HTTPError:
                return

        if r.status_code != 200:
            return
        html_lower = r.text.lower()
        exists_markers = ["continue to chat", "continuar al chat", "abrir en whatsapp", "open in whatsapp"]
        not_exists_markers = ["el número de teléfono compartido", "the phone number shared", "inválido"]

        base_payload = {
            "phone": digits,
            "country_code": country_code,
            "country_prefix": country_prefix,
            "canonical_redirect": canonical_redirect,
            "api_send_redirects_to_wa": api_redirects_to_wa,
        }

        if any(m in html_lower for m in exists_markers):
            confidence = 0.8 if api_redirects_to_wa else 0.7
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"Número activo en WhatsApp ({digits})",
                url=url,
                confidence=confidence,
                payload={**base_payload, "status": "exists"},
            )
        elif any(m in html_lower for m in not_exists_markers):
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"Número NO detectado en WhatsApp ({digits})",
                url=url,
                confidence=0.6,
                payload={**base_payload, "status": "absent"},
            )
        else:
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"WhatsApp estado inconcluso ({digits})",
                url=url,
                confidence=0.3,
                payload={**base_payload, "status": "unknown"},
            )
