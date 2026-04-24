"""Keybase user lookup (free public API)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class KeybaseCollector(Collector):
    name = "keybase"
    category = "social"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        async with client(timeout=12) as c:
            try:
                r = await c.get(
                    "https://keybase.io/_/api/1.0/user/lookup.json",
                    params={"usernames": u, "fields": "basics,profile,proofs_summary"},
                )
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        data = r.json()
        for them in data.get("them", []) or []:
            if not them:
                continue
            basics = them.get("basics", {}) or {}
            profile = them.get("profile", {}) or {}
            proofs = them.get("proofs_summary", {}).get("all", []) or []
            yield Finding(
                collector=self.name,
                category="username",
                entity_type="KeybaseProfile",
                title=f"Keybase: {basics.get('username')} ({profile.get('full_name') or ''})",
                url=f"https://keybase.io/{basics.get('username')}",
                confidence=0.9,
                payload={
                    "username": basics.get("username"),
                    "full_name": profile.get("full_name"),
                    "bio": profile.get("bio"),
                    "location": profile.get("location"),
                    "proofs": [{"service": p.get("proof_type"), "name": p.get("nametag"), "url": p.get("service_url")} for p in proofs],
                },
            )
