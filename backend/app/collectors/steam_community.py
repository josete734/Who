"""Steam Community public profile scrape (HTML + ?xml=1)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class SteamCommunityCollector(Collector):
    name = "steam_community"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://steamcommunity.com/id/{u}"
        xml_url = f"{url}?xml=1"
        c = await get_client("default")
        try:
            try:
                r = await c.get(xml_url, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code == 404 or r.status_code != 200:
                return
            xml_text = r.text
            if "<error>" in xml_text.lower() or "<profile>" not in xml_text.lower():
                return
            soup = BeautifulSoup(xml_text, "lxml-xml")
            persona = soup.find("steamID")
            real_name = soup.find("realname")
            location = soup.find("location")
            member_since = soup.find("memberSince")
            primary_clan = soup.find("primaryGroupID")
            try:
                rg = await c.get(f"{url}/games/?tab=all&xml=1", timeout=12.0)
                games_count = None
                if rg.status_code == 200:
                    games_count = rg.text.lower().count("<game>")
            except httpx.HTTPError:
                games_count = None
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Steam: {persona.text if persona else u}", url=url, confidence=0.65,
                payload={"platform": "steam", "username": u,
                         "persona_name": persona.text if persona else None,
                         "real_name": real_name.text if real_name else None,
                         "location": location.text if location else None,
                         "member_since": member_since.text if member_since else None,
                         "games_owned": games_count,
                         "primary_clan": primary_clan.text if primary_clan else None},
            )
        finally:
            await c.aclose()
