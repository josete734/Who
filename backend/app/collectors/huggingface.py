"""Hugging Face profile collector (Wave 8).

``GET https://huggingface.co/api/users/{username}`` returns a public JSON
profile with full name, bio, organisations, and counts of authored models
/ datasets / spaces. No key required, no rate limit beyond standard HF
fair-use.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_BASE = "https://huggingface.co/api/users/"


@register
class HuggingFaceProfileCollector(Collector):
    name = "huggingface"
    category = "username"
    needs = ("username",)
    timeout_seconds = 15
    description = "Hugging Face public profile (models, datasets, spaces)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")

        async with client(timeout=12) as c:
            try:
                r = await c.get(f"{_BASE}{u}/overview")
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return
        if not isinstance(data, dict):
            return

        # The overview endpoint groups summary, models, datasets, spaces.
        user = data.get("user") or data
        if not isinstance(user, dict):
            return

        # Identity guard: confirm the response is for the requested user.
        returned = (user.get("user") or user.get("username") or "").lower()
        if returned and returned != u.lower():
            return

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="HuggingFaceProfile",
            title=f"Hugging Face: @{u} ({user.get('fullname') or user.get('user') or u})",
            url=f"https://huggingface.co/{u}",
            confidence=0.92,
            payload={
                "username": u,
                "full_name": user.get("fullname"),
                "avatar": user.get("avatarUrl"),
                "is_pro": user.get("isPro"),
                "n_models": (data.get("modelsCount") or user.get("numModels")),
                "n_datasets": (data.get("datasetsCount") or user.get("numDatasets")),
                "n_spaces": (data.get("spacesCount") or user.get("numSpaces")),
                "n_followers": user.get("numFollowers"),
                "github": user.get("githubUser"),
                "twitter": user.get("twitterUser"),
                "linkedin": user.get("linkedinUser"),
                "orgs": [o.get("name") for o in (user.get("orgs") or []) if isinstance(o, dict)],
            },
        )
