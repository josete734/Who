"""Last.fm public profile scrape."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


@register
class LastfmCollector(Collector):
    name = "lastfm"
    category = "lifestyle"
    needs = ("username",)
    timeout_seconds = 12

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        base = f"https://www.last.fm/user/{u}"
        artists_url = f"{base}/library/artists?date_preset=LAST_7_DAYS"
        c = await get_client("default")
        try:
            try:
                r = await c.get(base, timeout=12.0)
            except httpx.HTTPError:
                return
            if r.status_code == 404 or r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "lxml")
            scrobble = soup.find(["a", "li"], class_=re.compile(r"scrobble"))
            scrobble_count = None
            mscr = re.search(r"([\d,\.]+)\s*scrobbles?", soup.get_text(" ", strip=True), re.I)
            if mscr:
                scrobble_count = int(mscr.group(1).replace(",", "").replace(".", ""))
            registered = None
            mreg = re.search(r"Scrobbling since\s+(\d+\s+\w+\s+\d{4})", soup.get_text(" ", strip=True))
            if mreg:
                registered = mreg.group(1)
            country = None
            ctag = soup.find("span", class_=re.compile(r"country|location"))
            if ctag:
                country = ctag.get_text(strip=True)
            top_artists: list[str] = []
            try:
                ra = await c.get(artists_url, timeout=12.0)
                if ra.status_code == 200:
                    s2 = BeautifulSoup(ra.text, "lxml")
                    for a in s2.select("td.chartlist-name a")[:10]:
                        top_artists.append(a.get_text(strip=True))
            except httpx.HTTPError:
                pass
            yield Finding(
                collector=self.name, category="lifestyle", entity_type="account",
                title=f"Last.fm: {u}", url=base, confidence=0.65,
                payload={"platform": "lastfm", "username": u, "scrobble_count": scrobble_count,
                         "top_artists_7d": top_artists, "registered_since": registered, "country": country},
            )
        finally:
            await c.aclose()
