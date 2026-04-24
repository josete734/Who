"""wa.me WhatsApp existence check.

Hitting https://wa.me/<num> returns different page contents depending on whether
the number is in WhatsApp or not. This is passive (no message sent).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import phonenumbers

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
        url = f"https://wa.me/{digits}"
        async with client(timeout=12) as c:
            r = await c.get(url)
        if r.status_code != 200:
            return
        html_lower = r.text.lower()
        # Heuristic: WA landing for existing numbers loads a "Continuar al chat" / "Continue to chat" button.
        exists_markers = ["continue to chat", "continuar al chat", "abrir en whatsapp", "open in whatsapp"]
        not_exists_markers = ["el número de teléfono compartido", "the phone number shared", "inválido"]
        if any(m in html_lower for m in exists_markers):
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"Número activo en WhatsApp ({digits})",
                url=url,
                confidence=0.7,
                payload={"phone": digits, "status": "exists"},
            )
        elif any(m in html_lower for m in not_exists_markers):
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"Número NO detectado en WhatsApp ({digits})",
                url=url,
                confidence=0.6,
                payload={"phone": digits, "status": "absent"},
            )
        else:
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="WhatsAppPresence",
                title=f"WhatsApp estado inconcluso ({digits})",
                url=url,
                confidence=0.3,
                payload={"phone": digits, "status": "unknown"},
            )
