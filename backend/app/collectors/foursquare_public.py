"""Foursquare/Swarm public profile checkins (Wave 4 / A4.1).

Scrapes ``https://foursquare.com/{username}`` HTML for visible checkin
venues. Each venue is emitted as a ``checkin`` finding with whatever
location signals we can extract from the rendered DOM.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


PROFILE_URL = "https://foursquare.com/{username}"


def _parse_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"name": None, "bio": None, "checkins": []}

    title = soup.find("meta", attrs={"property": "og:title"})
    if title and title.get("content"):
        out["name"] = title["content"].strip()
    desc = soup.find("meta", attrs={"property": "og:description"})
    if desc and desc.get("content"):
        out["bio"] = desc["content"].strip()

    # JSON-LD blocks
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("Place", "LocalBusiness", "Restaurant"):
                addr = item.get("address") or {}
                geo = item.get("geo") or {}
                out["checkins"].append(
                    {
                        "venue": item.get("name"),
                        "address": addr.get("streetAddress") if isinstance(addr, dict) else None,
                        "city": addr.get("addressLocality") if isinstance(addr, dict) else None,
                        "country": addr.get("addressCountry") if isinstance(addr, dict) else None,
                        "lat": geo.get("latitude") if isinstance(geo, dict) else None,
                        "lon": geo.get("longitude") if isinstance(geo, dict) else None,
                        "url": item.get("url"),
                    }
                )

    # DOM fallback: venue links typically /v/<slug>/<id>
    for a in soup.select('a[href*="/v/"]'):
        href = a.get("href") or ""
        text = a.get_text(" ", strip=True)
        if not text or len(text) > 120:
            continue
        m = re.match(r"^/v/([^/]+)/([0-9a-f]{8,})", href)
        if not m:
            continue
        out["checkins"].append(
            {
                "venue": text,
                "url": f"https://foursquare.com{href}",
                "venue_id": m.group(2),
            }
        )
        if len(out["checkins"]) >= 50:
            break

    return out


@register
class FoursquarePublicCollector(Collector):
    name = "foursquare_public"
    category = "geo"
    needs = ("username",)
    timeout_seconds = 30
    description = "Foursquare public profile -> visible checkins (HTML scrape)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        username = input.username.lstrip("@")
        url = PROFILE_URL.format(username=username)

        client = await get_client("gentle")
        try:
            try:
                r = await client.get(url)
            except httpx.HTTPError:
                return
        finally:
            await client.aclose()

        if r.status_code != 200:
            return

        data = _parse_html(r.text or "")
        if not data["checkins"] and not data["name"]:
            return

        # Profile finding (account-level)
        yield Finding(
            collector=self.name,
            category="geo",
            entity_type="account",
            title=f"Foursquare: {data.get('name') or username}",
            url=url,
            confidence=0.6,
            payload={
                "platform": "foursquare",
                "username": username,
                "name": data.get("name"),
                "bio": data.get("bio"),
                "checkins_count": len(data["checkins"]),
            },
        )

        seen: set[str] = set()
        for ck in data["checkins"]:
            key = (ck.get("venue_id") or "") + "|" + (ck.get("venue") or "")
            if key in seen:
                continue
            seen.add(key)
            yield Finding(
                collector=self.name,
                category="geo",
                entity_type="checkin",
                title=f"Foursquare checkin: {ck.get('venue') or 'unknown'}",
                url=ck.get("url"),
                confidence=0.6,
                payload={
                    "platform": "foursquare",
                    "username": username,
                    **ck,
                },
            )


__all__ = ["FoursquarePublicCollector", "PROFILE_URL", "_parse_html"]
