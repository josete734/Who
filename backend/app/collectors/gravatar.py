"""Gravatar collector.

Given an email, fetches the Gravatar profile (public profile associated to email
MD5 *and* SHA256). Modern Gravatar accepts SHA256 hashes; we lookup both and
emit one finding per matching hash.
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
    description = "Public Gravatar profile (bio, name, accounts) tied to the email MD5/SHA256."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        emails = input.emails()
        if not emails:
            return

        async with client(timeout=10) as c:
            for email in emails:
                normalized = email.lower().strip().encode()
                md5 = hashlib.md5(normalized).hexdigest()  # noqa: S324
                sha256 = hashlib.sha256(normalized).hexdigest()
                for kind, h, profile_url in [
                    ("md5", md5, f"https://en.gravatar.com/{md5}.json"),
                    ("sha256", sha256, f"https://gravatar.com/{sha256}.json"),
                ]:
                    try:
                        r = await c.get(profile_url)
                    except httpx.HTTPError:
                        continue
                    if r.status_code == 404:
                        # Try avatar 404 endpoint as a soft check (no finding emitted on miss).
                        continue
                    if r.status_code != 200:
                        continue
                    try:
                        data = r.json()
                    except ValueError:
                        continue
                    for entry in data.get("entry", []):
                        name = entry.get("name", {})
                        display_name = name.get("formatted") or entry.get("displayName") or email
                        yield Finding(
                            collector=self.name,
                            category="email",
                            entity_type="GravatarProfile",
                            title=f"Gravatar de {display_name} ({kind})",
                            url=entry.get("profileUrl"),
                            confidence=0.95,
                            payload={
                                "email": email,
                                "hash": h,
                                "hash_kind": kind,
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
