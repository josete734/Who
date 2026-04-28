"""Bluesky public profile lookup via AT Protocol app-view (no auth needed)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.netfetch import get_client  # re-exported for callers/tests
from app.schemas import SearchInput

BSKY_BASE = "https://public.api.bsky.app/xrpc"


async def _safe_get(c: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    try:
        r = await c.get(url, params=params)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


@register
class BlueskyCollector(Collector):
    name = "bluesky"
    category = "social"
    needs = ("username",)
    timeout_seconds = 30

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        candidates = [u, f"{u}.bsky.social"]
        async with client(timeout=12) as c:
            profile_data: dict | None = None
            handle_used: str | None = None
            for actor in candidates:
                p = await _safe_get(
                    c, f"{BSKY_BASE}/app.bsky.actor.getProfile", {"actor": actor}
                )
                if not p:
                    continue
                profile_data = p
                handle_used = p.get("handle") or actor
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

            if not profile_data:
                return
            did = profile_data.get("did") or handle_used

            # Author feed (recent posts)
            feed = await _safe_get(
                c, f"{BSKY_BASE}/app.bsky.feed.getAuthorFeed", {"actor": did, "limit": 50}
            )
            if feed:
                for item in feed.get("feed", []) or []:
                    post = item.get("post", {}) or {}
                    record = post.get("record", {}) or {}
                    uri = post.get("uri", "")
                    rkey = uri.rsplit("/", 1)[-1] if uri else ""
                    yield Finding(
                        collector=self.name,
                        category="social",
                        entity_type="post",
                        title=f"Bluesky post by @{handle_used}",
                        url=f"https://bsky.app/profile/{handle_used}/post/{rkey}" if rkey else None,
                        confidence=0.8,
                        payload={
                            "uri": uri,
                            "cid": post.get("cid"),
                            "ts": record.get("createdAt"),
                            "text": record.get("text"),
                            "like_count": post.get("likeCount"),
                            "repost_count": post.get("repostCount"),
                            "reply_count": post.get("replyCount"),
                        },
                    )

            # Followers
            followers = await _safe_get(
                c, f"{BSKY_BASE}/app.bsky.graph.getFollowers", {"actor": did, "limit": 100}
            )
            if followers:
                for f in followers.get("followers", []) or []:
                    yield Finding(
                        collector=self.name,
                        category="social",
                        entity_type="account",
                        title=f"Bluesky follower: @{f.get('handle')}",
                        url=f"https://bsky.app/profile/{f.get('handle')}",
                        confidence=0.7,
                        payload={
                            "relation": "follower",
                            "of": handle_used,
                            "handle": f.get("handle"),
                            "did": f.get("did"),
                            "display_name": f.get("displayName"),
                        },
                    )

            # Follows
            follows = await _safe_get(
                c, f"{BSKY_BASE}/app.bsky.graph.getFollows", {"actor": did, "limit": 100}
            )
            if follows:
                for f in follows.get("follows", []) or []:
                    yield Finding(
                        collector=self.name,
                        category="social",
                        entity_type="account",
                        title=f"Bluesky follow: @{f.get('handle')}",
                        url=f"https://bsky.app/profile/{f.get('handle')}",
                        confidence=0.7,
                        payload={
                            "relation": "follow",
                            "of": handle_used,
                            "handle": f.get("handle"),
                            "did": f.get("did"),
                            "display_name": f.get("displayName"),
                        },
                    )


__all__ = ["BlueskyCollector", "BSKY_BASE", "get_client"]
