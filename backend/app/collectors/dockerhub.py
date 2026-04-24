"""Docker Hub public user lookup."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class DockerHubCollector(Collector):
    name = "dockerhub"
    category = "code"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        async with client(timeout=12) as c:
            try:
                r = await c.get(f"https://hub.docker.com/v2/users/{u}/")
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        d = r.json()
        yield Finding(
            collector=self.name,
            category="username",
            entity_type="DockerHubProfile",
            title=f"Docker Hub: {d.get('username')} ({d.get('full_name') or ''})",
            url=f"https://hub.docker.com/u/{d.get('username')}",
            confidence=0.9,
            payload={
                "username": d.get("username"),
                "full_name": d.get("full_name"),
                "company": d.get("company"),
                "location": d.get("location"),
                "date_joined": d.get("date_joined"),
                "profile_url": d.get("profile_url"),
            },
        )
