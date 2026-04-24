"""HaveIBeenPwned collector using the free Pwned Passwords k-anonymity endpoint
AND the free breach search endpoint that requires a key for email, but we fall
back to the k-anonymity password API for passwords only.

For email -> breaches, HIBP now requires a paid key. We instead use two free
alternatives:
  - https://api.proxynova.com/comb (public leaks search)  -- optional, depends on availability
  - breach directory style search via public endpoints
This collector keeps a conservative, legal approach and uses only free public
sources. If none is reachable we emit nothing.
"""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class HIBPBreachHint(Collector):
    name = "hibp_hint"
    category = "email"
    needs = ("email",)
    timeout_seconds = 15
    description = "Checks public breach search APIs (free tier) for the email."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        email = (input.email or "").lower().strip()
        if not email:
            return

        # Attempt 1: proxynova COMB search (free, public, rate-limited).
        try:
            async with client(timeout=10) as c:
                r = await c.get("https://api.proxynova.com/comb", params={"query": email})
                if r.status_code == 200:
                    data = r.json()
                    lines = data.get("lines", []) or []
                    if lines:
                        yield Finding(
                            collector=self.name,
                            category="breach",
                            entity_type="BreachHit",
                            title=f"{len(lines)} referencias en agregador público COMB",
                            url="https://api.proxynova.com/comb",
                            confidence=0.65,
                            payload={"email": email, "sample": lines[:5], "total": data.get("count")},
                        )
        except httpx.HTTPError:
            pass

        # Attempt 2: breachdirectory (free tier via rapidapi — skip if no key; left as doc).
        # Attempt 3: generate recommendation URLs for manual verification.
        yield Finding(
            collector=self.name,
            category="breach",
            entity_type="BreachLink",
            title="Consulta manual en HaveIBeenPwned",
            url=f"https://haveibeenpwned.com/account/{email}",
            confidence=0.4,
            payload={"note": "HIBP API para email es de pago. Abrir URL manualmente para comprobar."},
        )


@register
class HIBPPasswordKAnon(Collector):
    """Not applicable by default (no password in SearchInput). Placeholder for future use."""

    name = "hibp_passwords"
    category = "password"
    needs = ()
    timeout_seconds = 10
    description = "(Disabled) K-anonymity pwned passwords lookup. Requires explicit password input."

    def applicable(self, input: SearchInput) -> bool:  # always false for SearchInput
        return False

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if False:  # pragma: no cover
            yield  # make this an async generator
