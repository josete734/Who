"""Gravatar collector.

Given an email, fetches the Gravatar profile (public profile associated to email MD5).
Free, no auth.
"""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class GravatarCollector(Collector):
    name = "gravatar"
    category = "email"
    needs = ("email",)
    timeout_seconds = 15
    description = "Public Gravatar profile (bio, name, accounts) tied to the email MD5."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.email is not None
        md5 = hashlib.md5(input.email.lower().strip().encode()).hexdigest()  # noqa: S324
        url = f"https://en.gravatar.com/{md5}.json"
        async with client(timeout=10) as c:
            try:
                r = await c.get(url)
            except httpx.HTTPError as e:
                raise RuntimeError(f"Gravatar fetch failed: {e}") from e
        if r.status_code == 404:
            return
        if r.status_code != 200:
            raise RuntimeError(f"Gravatar HTTP {r.status_code}")
        data = r.json()
        for entry in data.get("entry", []):
            name = entry.get("name", {})
            display_name = name.get("formatted") or entry.get("displayName") or input.email
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="GravatarProfile",
                title=f"Gravatar de {display_name}",
                url=entry.get("profileUrl"),
                confidence=0.95,
                payload={
                    "hash": md5,
                    "preferred_username": entry.get("preferredUsername"),
                    "name": name,
                    "about": entry.get("aboutMe"),
                    "location": entry.get("currentLocation"),
                    "accounts": entry.get("accounts", []),
                    "emails": entry.get("emails", []),
                    "urls": entry.get("urls", []),
                    "photos": entry.get("photos", []),
                },
            )
