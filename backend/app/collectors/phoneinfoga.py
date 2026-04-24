"""PhoneInfoga-lite collector.

Pure-Python re-implementation of PhoneInfoga's `local` + `googlesearch` scanners.
- `local`: parses the number with libphonenumber to extract country, carrier, type, timezone.
- `googlesearch`: builds useful Google dorks as URLs so the user can open manually.
Also:
- Verifies WhatsApp existence via wa.me HEAD request (handled in wa_me.py).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import phonenumbers
from phonenumbers import carrier as pn_carrier
from phonenumbers import geocoder as pn_geocoder
from phonenumbers import timezone as pn_timezone

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput


def _dorks(phone_e164: str, raw: str) -> list[tuple[str, str]]:
    q_e164 = phone_e164.replace("+", "%2B")
    raw_safe = raw.replace(" ", "")
    return [
        ("Google general", f"https://www.google.com/search?q=%22{q_e164}%22+OR+%22{raw_safe}%22"),
        ("Bing general", f"https://www.bing.com/search?q=%22{q_e164}%22"),
        ("LinkedIn", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Alinkedin.com"),
        ("Facebook", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Afacebook.com"),
        ("X / Twitter", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Ax.com+OR+site%3Atwitter.com"),
        ("Instagram", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Ainstagram.com"),
        ("TikTok", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Atiktok.com"),
        ("Telegram (t.me)", f"https://www.google.com/search?q=%22{q_e164}%22+site%3At.me"),
        ("Wallapop", f"https://www.google.com/search?q=%22{raw_safe}%22+site%3Awallapop.com"),
        ("Milanuncios", f"https://www.google.com/search?q=%22{raw_safe}%22+site%3Amilanuncios.com"),
        ("Idealista", f"https://www.google.com/search?q=%22{raw_safe}%22+site%3Aidealista.com"),
        ("Pastebin", f"https://www.google.com/search?q=%22{q_e164}%22+site%3Apastebin.com"),
        ("Truecaller", f"https://www.truecaller.com/search/es/{phone_e164.lstrip('+')}"),
    ]


@register
class PhoneInfogaCollector(Collector):
    name = "phoneinfoga"
    category = "phone"
    needs = ("phone",)
    timeout_seconds = 10
    description = "PhoneInfoga-style local analysis + Google dorks."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.phone
        raw = input.phone.strip()
        try:
            num = phonenumbers.parse(raw, None if raw.startswith("+") else "ES")
        except phonenumbers.NumberParseException as e:
            raise RuntimeError(f"No se pudo parsear el número: {e}") from e
        if not phonenumbers.is_possible_number(num):
            raise RuntimeError("Número no válido")

        e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        intl = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        country = phonenumbers.region_code_for_number(num) or "?"
        typ = phonenumbers.number_type(num)
        _TYPE_NAMES = {
            0: "FIXED_LINE", 1: "MOBILE", 2: "FIXED_LINE_OR_MOBILE", 3: "TOLL_FREE",
            4: "PREMIUM_RATE", 5: "SHARED_COST", 6: "VOIP", 7: "PERSONAL_NUMBER",
            8: "PAGER", 9: "UAN", 10: "VOICEMAIL", 27: "UNKNOWN",
        }
        typ_name = _TYPE_NAMES.get(int(typ), str(typ))
        loc = pn_geocoder.description_for_number(num, "es") or ""
        car = pn_carrier.name_for_number(num, "es") or ""
        tzs = list(pn_timezone.time_zones_for_number(num))

        yield Finding(
            collector=self.name,
            category="phone",
            entity_type="PhoneMetadata",
            title=f"{intl} ({country}, {car or 'operador desconocido'})",
            url=None,
            confidence=1.0,
            payload={
                "e164": e164,
                "international": intl,
                "country_code": country,
                "type": typ_name,
                "location": loc,
                "carrier": car,
                "timezones": tzs,
                "valid": phonenumbers.is_valid_number(num),
            },
        )

        for label, url in _dorks(e164, raw):
            yield Finding(
                collector=self.name,
                category="phone",
                entity_type="Dork",
                title=f"Dork: {label}",
                url=url,
                confidence=0.3,
                payload={"engine": label},
            )
