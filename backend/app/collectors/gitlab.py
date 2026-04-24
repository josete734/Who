"""GitLab public user search (no token required for limited queries)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class GitLabCollector(Collector):
    name = "gitlab"
    category = "code"
    needs = ("username", "email")
    timeout_seconds = 20

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        targets: list[tuple[str, str]] = []
        if input.username:
            targets.append(("username", input.username.lstrip("@")))
        if input.email:
            targets.append(("search", input.email))

        async with client(timeout=15) as c:
            for mode, value in targets:
                try:
                    r = await c.get("https://gitlab.com/api/v4/users", params={mode: value})
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                for u in (r.json() or [])[:5]:
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="GitLabProfile",
                        title=f"GitLab: {u.get('username')} ({u.get('name') or ''})",
                        url=u.get("web_url"),
                        confidence=0.85,
                        payload={
                            "username": u.get("username"),
                            "name": u.get("name"),
                            "bio": u.get("bio"),
                            "location": u.get("location"),
                            "organization": u.get("organization"),
                            "created_at": u.get("created_at"),
                        },
                    )
