"""Stack Exchange (Stack Overflow) user search by username or display name."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class StackExchangeCollector(Collector):
    name = "stackexchange"
    category = "code"
    needs = ("full_name", "birth_name", "aliases", "username")
    timeout_seconds = 30

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        queries: list[tuple[str, bool]] = []  # (value, is_username_match)
        if input.username:
            queries.append((input.username.lstrip("@"), True))
        for n in input.name_variants():
            queries.append((n, False))
        if not queries:
            return
        seen: set[int] = set()
        async with client(timeout=12) as c:
            for q, is_user in queries[:4]:
                try:
                    r = await c.get(
                        "https://api.stackexchange.com/2.3/users",
                        params={"inname": q, "site": "stackoverflow", "pagesize": 10,
                                "order": "desc", "sort": "reputation"},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                for it in (r.json().get("items") or [])[:5]:
                    uid = it.get("user_id")
                    if uid in seen:
                        continue
                    seen.add(uid)
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="StackOverflowUser",
                        title=f"Stack Overflow: {it.get('display_name')} ({it.get('reputation')} rep)",
                        url=it.get("link"),
                        confidence=0.8 if is_user else 0.55,
                        payload={
                            "user_id": it.get("user_id"),
                            "reputation": it.get("reputation"),
                            "location": it.get("location"),
                            "website_url": it.get("website_url"),
                            "creation_date": it.get("creation_date"),
                            "account_id": it.get("account_id"),
                            "matched_query": q,
                        },
                    )
