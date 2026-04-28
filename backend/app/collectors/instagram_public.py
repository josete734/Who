"""Instagram public profile via the web_profile_info endpoint.

Inspired by Osintgram (Datalux). No cookie needed for public basic info,
though Meta aggressively rate-limits anonymous requests. Falls back to
og:image scraping when the JSON endpoint is blocked.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class InstagramPublicCollector(Collector):
    name = "instagram_public"
    category = "social"
    needs = ("username",)
    timeout_seconds = 25
    description = "Instagram public profile (JSON endpoint + OG tags fallback)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        async with client(
            timeout=15,
            headers={
                "x-ig-app-id": "936619743392459",
                "User-Agent": "Instagram 219.0.0.12.117 Android (30/11; 420dpi; 1080x2400; samsung; SM-G996U; p3q; qcom; en_US; 344179499)",
            },
        ) as c:
            try:
                r = await c.get(f"https://i.instagram.com/api/v1/users/web_profile_info/?username={u}")
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                try:
                    data = r.json().get("data", {}).get("user")
                except ValueError:
                    data = None
                if data:
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="InstagramProfile",
                        title=f"Instagram: @{data.get('username')} ({data.get('full_name') or ''})",
                        url=f"https://www.instagram.com/{data.get('username')}/",
                        confidence=0.9,
                        payload={
                            "username": data.get("username"),
                            "full_name": data.get("full_name"),
                            "bio": data.get("biography"),
                            "id": data.get("id"),
                            "is_private": data.get("is_private"),
                            "is_verified": data.get("is_verified"),
                            "is_business_account": data.get("is_business_account"),
                            "profile_pic_url": data.get("profile_pic_url_hd") or data.get("profile_pic_url"),
                            "followers": (data.get("edge_followed_by") or {}).get("count"),
                            "following": (data.get("edge_follow") or {}).get("count"),
                            "posts": (data.get("edge_owner_to_timeline_media") or {}).get("count"),
                            "category": data.get("category_name"),
                            "external_url": data.get("external_url"),
                        },
                    )
                    return

            # Fallback: scrape og: tags from public page
            try:
                r = await c.get(f"https://www.instagram.com/{u}/")
            except httpx.HTTPError:
                return
            if r.status_code != 200:
                return
            html = r.text
            # Bail on the login-wall / "user not found" pages — Instagram still emits
            # generic OG tags for these and we'd otherwise emit a false-positive finding.
            if "Sorry, this page isn" in html or ("login_popup" in html and f"@{u}" not in html):
                return
            og_title = _pick(html, r'<meta property="og:title" content="([^"]+)"')
            og_desc = _pick(html, r'<meta property="og:description" content="([^"]+)"')
            og_img = _pick(html, r'<meta property="og:image" content="([^"]+)"')
            # Identity guard: emit only if the username clearly appears in the OG title.
            if og_title and u.lower() in og_title.lower():
                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="InstagramProfile",
                    title=f"Instagram (OG): {og_title[:160]}",
                    url=f"https://www.instagram.com/{u}/",
                    confidence=0.7,
                    payload={
                        "og_title": og_title,
                        "og_description": og_desc,
                        "profile_pic_url": og_img,
                        "source": "og_tags_fallback",
                    },
                )


def _pick(html: str, pat: str) -> str | None:
    m = re.search(pat, html)
    return m.group(1) if m else None
