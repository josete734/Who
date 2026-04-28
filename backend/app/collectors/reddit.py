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
from app.netfetch import get_client
from app.schemas import SearchInput

MAX_ITEMS = 500
MAX_PAGES = 5


def _excerpt(text: str | None, n: int = 280) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


async def _paginate(c: httpx.AsyncClient, url: str) -> list[dict]:
    """Yield up to MAX_ITEMS via Reddit listing pagination (after token)."""
    items: list[dict] = []
    after: str | None = None
    for _ in range(MAX_PAGES):
        params = {"limit": "100"}
        if after:
            params["after"] = after
        try:
            r = await c.get(url, params=params)
        except httpx.HTTPError:
            break
        if r.status_code != 200:
            break
        try:
            data = r.json().get("data", {})
        except ValueError:
            break
        children = data.get("children", []) or []
        for ch in children:
            items.append(ch.get("data", {}))
            if len(items) >= MAX_ITEMS:
                return items
        after = data.get("after")
        if not after:
            break
    return items


@register
class RedditUserCollector(Collector):
    name = "reddit"
    category = "social"
    needs = ("username",)
    timeout_seconds = 60
    description = "Reddit public profile + recent submissions/comments via reddit.com/user/<name>"

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        s = get_settings()
        headers = {"User-Agent": s.reddit_user_agent or "osint-tool/1.0"}
        u = input.username
        async with client(timeout=15, headers=headers) as c:
            try:
                r = await c.get(f"https://www.reddit.com/user/{u}/about.json")
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

            # Submissions
            for item in await _paginate(c, f"https://www.reddit.com/user/{u}/submitted.json"):
                yield Finding(
                    collector=self.name,
                    category="social",
                    entity_type="post",
                    title=f"Reddit post: {_excerpt(item.get('title'), 80)}",
                    url=f"https://www.reddit.com{item.get('permalink', '')}" if item.get("permalink") else None,
                    confidence=0.8,
                    payload={
                        "subreddit": item.get("subreddit"),
                        "ts": item.get("created_utc"),
                        "title": item.get("title"),
                        "body_excerpt": _excerpt(item.get("selftext")),
                        "score": item.get("score"),
                    },
                )
            # Comments
            for item in await _paginate(c, f"https://www.reddit.com/user/{u}/comments.json"):
                yield Finding(
                    collector=self.name,
                    category="social",
                    entity_type="comment",
                    title=f"Reddit comment in r/{item.get('subreddit')}",
                    url=f"https://www.reddit.com{item.get('permalink', '')}" if item.get("permalink") else None,
                    confidence=0.8,
                    payload={
                        "subreddit": item.get("subreddit"),
                        "ts": item.get("created_utc"),
                        "title": item.get("link_title"),
                        "body_excerpt": _excerpt(item.get("body")),
                        "score": item.get("score"),
                    },
                )


__all__ = ["RedditUserCollector", "_paginate", "_excerpt", "MAX_ITEMS", "MAX_PAGES", "get_client"]
