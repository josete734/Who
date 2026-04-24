"""Mastodon WebFinger probe on popular instances."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

INSTANCES = [
    "mastodon.social", "mas.to", "mstdn.social", "hachyderm.io", "fosstodon.org",
    "tech.lgbt", "infosec.exchange", "ioc.exchange", "sigmoid.social", "ohai.social",
    "masto.es", "mastodon.world",
]


@register
class MastodonWebFingerCollector(Collector):
    name = "mastodon"
    category = "social"
    needs = ("username",)
    timeout_seconds = 60

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        async with client(timeout=8) as c:
            for inst in INSTANCES:
                try:
                    r = await c.get(
                        f"https://{inst}/.well-known/webfinger",
                        params={"resource": f"acct:{u}@{inst}"},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                links = data.get("links", []) or []
                profile = next((lnk.get("href") for lnk in links if lnk.get("rel") == "http://webfinger.net/rel/profile-page"), None) or f"https://{inst}/@{u}"
                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="MastodonProfile",
                    title=f"Mastodon: @{u}@{inst}",
                    url=profile,
                    confidence=0.85,
                    payload={"subject": data.get("subject"), "aliases": data.get("aliases", [])},
                )
