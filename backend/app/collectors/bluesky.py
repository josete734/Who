"""Bluesky public profile lookup via AT Protocol app-view (no auth needed)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class BlueskyCollector(Collector):
    name = "bluesky"
    category = "social"
    needs = ("username",)
    timeout_seconds = 20

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        candidates = [u, f"{u}.bsky.social"]
        async with client(timeout=12) as c:
            for actor in candidates:
                try:
                    r = await c.get(
                        "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                        params={"actor": actor},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                p = r.json()
                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="BlueskyProfile",
                    title=f"Bluesky: {p.get('displayName') or p.get('handle')}",
                    url=f"https://bsky.app/profile/{p.get('handle')}",
                    confidence=0.9,
                    payload={
                        "handle": p.get("handle"),
                        "did": p.get("did"),
                        "display_name": p.get("displayName"),
                        "description": p.get("description"),
                        "followers": p.get("followersCount"),
                        "follows": p.get("followsCount"),
                        "posts": p.get("postsCount"),
                        "created_at": p.get("createdAt"),
                    },
                )
                break
