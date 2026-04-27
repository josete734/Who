"""Skype username validator.

Hits ``https://login.skype.com/json/validator?new_username={u}``. The endpoint
is meant for live availability checks during signup: when the username is
already taken (status indicates "not available" / status != 0), we treat it as
positive evidence that a Skype account exists for that handle.

Note: ``messengers_extra`` already exposes a similar probe; this collector is
a focused, standalone variant per the Wave 3 plan.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

SKYPE_VALIDATOR_URL = "https://login.skype.com/json/validator"


@register
class SkypeValidatorCollector(Collector):
    name = "skype_validator"
    category = "username"
    needs = ("username",)
    timeout_seconds = 15
    description = "Skype account existence via login.skype.com signup validator."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@").strip()
        if not u:
            return

        c = await get_client("gentle")
        try:
            try:
                r = await c.get(SKYPE_VALIDATOR_URL, params={"new_username": u})
            except httpx.HTTPError:
                return
        finally:
            await c.aclose()

        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return

        # Skype endpoint convention: status==0 means "available" (account doesn't
        # exist); any non-zero status (often 1) plus a markup mentioning "not
        # available" / "ya está en uso" signals that the handle is taken.
        status = data.get("status")
        markup = (data.get("markup") or "").lower()
        taken = (status not in (0, "0", None)) or any(
            m in markup for m in ("not available", "no está disponible", "ya está en uso", "taken")
        )
        if not taken:
            return

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="SkypePresence",
            title=f"Skype: cuenta existente para '{u}'",
            url="https://www.skype.com/",
            confidence=0.55,
            payload={
                "platform": "skype",
                "username": u,
                "status": status,
                "markup": data.get("markup"),
                "exists": True,
            },
        )


__all__ = ["SkypeValidatorCollector", "SKYPE_VALIDATOR_URL"]
