"""Mastodon WebFinger probe on popular instances + ActivityPub profile resolution."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.netfetch import get_client  # re-exported for callers/tests
from app.schemas import SearchInput

INSTANCES = [
    "mastodon.social", "mas.to", "mstdn.social", "hachyderm.io", "fosstodon.org",
    "tech.lgbt", "infosec.exchange", "ioc.exchange", "sigmoid.social", "ohai.social",
    "masto.es", "mastodon.world",
]


def _self_href(links: list[dict]) -> str | None:
    """Pick the ActivityPub self link from WebFinger links."""
    for lnk in links or []:
        if lnk.get("rel") == "self" and "json" in (lnk.get("type") or "").lower():
            href = lnk.get("href")
            if href:
                return href
    return None


@register
class MastodonWebFingerCollector(Collector):
    name = "mastodon"
    category = "social"
    needs = ("username",)
    timeout_seconds = 90

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
                profile = next(
                    (lnk.get("href") for lnk in links if lnk.get("rel") == "http://webfinger.net/rel/profile-page"),
                    None,
                ) or f"https://{inst}/@{u}"

                # Resolve ActivityPub profile JSON. Prefer self-href from WebFinger; else fallback.
                ap_url = _self_href(links) or f"https://{inst}/users/{u}.json"
                ap_payload: dict = {}
                try:
                    rp = await c.get(
                        ap_url,
                        headers={"Accept": "application/activity+json, application/json"},
                    )
                    if rp.status_code == 200:
                        try:
                            ap = rp.json()
                        except ValueError:
                            ap = {}
                        if isinstance(ap, dict):
                            ap_payload = {
                                "ap_id": ap.get("id"),
                                "ap_type": ap.get("type"),
                                "preferred_username": ap.get("preferredUsername"),
                                "name": ap.get("name"),
                                "summary": ap.get("summary"),
                                "url": ap.get("url"),
                                "followers_url": ap.get("followers"),
                                "following_url": ap.get("following"),
                                "outbox_url": ap.get("outbox"),
                                "published": ap.get("published"),
                            }
                except httpx.HTTPError:
                    pass

                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="MastodonProfile",
                    title=f"Mastodon: @{u}@{inst}",
                    url=profile,
                    confidence=0.85,
                    payload={
                        "subject": data.get("subject"),
                        "aliases": data.get("aliases", []),
                        "instance": inst,
                        "activitypub": ap_payload,
                    },
                )


__all__ = ["MastodonWebFingerCollector", "INSTANCES", "_self_href", "get_client"]
