"""Reddit collector — best-effort public fetch.

Uses Reddit's public JSON endpoints (no OAuth needed for basic lookups).
If REDDIT_CLIENT_ID/SECRET are set we prefer asyncpraw, else fall back to JSON.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


@register
class RedditUserCollector(Collector):
    name = "reddit"
    category = "social"
    needs = ("username",)
    timeout_seconds = 30
    description = "Reddit public profile via reddit.com/user/<name>/about.json"

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        s = get_settings()
        headers = {"User-Agent": s.reddit_user_agent or "osint-tool/1.0"}
        async with client(timeout=15, headers=headers) as c:
            try:
                r = await c.get(f"https://www.reddit.com/user/{input.username}/about.json")
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            d = r.json().get("data", {})
        except ValueError:
            return
        yield Finding(
            collector=self.name,
            category="username",
            entity_type="RedditProfile",
            title=f"Reddit: u/{d.get('name')}",
            url=f"https://www.reddit.com/user/{d.get('name')}",
            confidence=0.9,
            payload={
                "name": d.get("name"),
                "karma_link": d.get("link_karma"),
                "karma_comment": d.get("comment_karma"),
                "created_utc": d.get("created_utc"),
                "verified": d.get("verified"),
                "has_verified_email": d.get("has_verified_email"),
                "is_mod": d.get("is_mod"),
                "subreddit": d.get("subreddit", {}).get("display_name"),
                "public_description": d.get("subreddit", {}).get("public_description"),
            },
        )
