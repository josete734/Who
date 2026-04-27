"""Spotify public profile scrape (open.spotify.com/user/{u})."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class SpotifyPublicCollector(Collector):
    name = "spotify_public"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://open.spotify.com/user/{u}"
        c = await get_client("default")
        try:
            try:
                r = await c.get(url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code == 404:
                return
            if r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            def meta(prop: str) -> str | None:
                tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
                return tag.get("content") if tag else None
            display_name = meta("og:title") or meta("profile:username") or u
            image = meta("og:image")
            desc = meta("og:description") or ""
            followers = playlists = None
            mf = re.search(r"([\d,\.]+)\s+Followers", desc, re.I)
            mp = re.search(r"([\d,\.]+)\s+Public Playlists?", desc, re.I)
            if mf:
                followers = int(mf.group(1).replace(",", "").replace(".", ""))
            if mp:
                playlists = int(mp.group(1).replace(",", "").replace(".", ""))
            yield Finding(
                collector=self.name,
                category="lifestyle",
                entity_type="account",
                title=f"Spotify: {display_name}",
                url=url,
                confidence=0.65,
                payload={"platform": "spotify", "username": u, "display_name": display_name,
                         "follower_count": followers, "public_playlists": playlists, "profile_image": image},
            )
        finally:
            await c.aclose()
