"""AllTrails public member profile collector (Wave 2 / A2.5)."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

PROFILE_URL = "https://www.alltrails.com/members/{username}"


def _extract_jsonld(body: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        body,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            out.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            out.append(data)
    return out


def _meta(body: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]+)"',
        body,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _stat(body: str, label: str) -> int | None:
    pattern = rf'(\d[\d,\.]*)\s*<[^>]*>\s*{re.escape(label)}'
    m = re.search(pattern, body, re.IGNORECASE)
    if not m:
        m = re.search(
            rf'{re.escape(label)}[^<\d]{{0,40}}(\d[\d,\.]*)', body, re.IGNORECASE
        )
    if not m:
        return None
    raw = m.group(1).replace(",", "").replace(".", "")
    try:
        return int(raw)
    except ValueError:
        return None


@register
class AllTrailsCollector(Collector):
    name = "alltrails"
    category = "sport"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        username = input.username.lstrip("@")
        url = PROFILE_URL.format(username=username)

        async with await get_client("gentle") as c:
            try:
                r = await c.get(url, timeout=12.0)
            except (httpx.HTTPError, OSError):
                return
        if r.status_code != 200:
            return
        body = r.text or ""

        name: str | None = None
        location: str | None = None
        photo_url: str | None = None
        for entity in _extract_jsonld(body):
            t = entity.get("@type")
            if t in ("Person", "ProfilePage"):
                name = name or entity.get("name")
                addr = entity.get("address")
                if isinstance(addr, dict):
                    location = location or addr.get("addressLocality") or addr.get("name")
                elif isinstance(addr, str):
                    location = location or addr
                img = entity.get("image")
                if isinstance(img, dict):
                    photo_url = photo_url or img.get("url")
                elif isinstance(img, str):
                    photo_url = photo_url or img

        if not name:
            name = _meta(body, "og:title") or _meta(body, "profile:username")
        if not photo_url:
            photo_url = _meta(body, "og:image")
        if not location:
            m = re.search(
                r'"location"\s*:\s*"([^"]{1,120})"', body
            )
            if m:
                location = m.group(1)

        trails_count = _stat(body, "Trails") or _stat(body, "trails")
        reviews_count = _stat(body, "Reviews") or _stat(body, "reviews")
        lists_count = _stat(body, "Lists") or _stat(body, "lists")

        if not name and trails_count is None and reviews_count is None:
            return

        payload: dict = {
            "platform": "alltrails",
            "username": username,
            "name": name,
            "location": location,
            "trails_count": trails_count,
            "reviews_count": reviews_count,
            "lists": lists_count,
        }
        if photo_url:
            payload["photo_url"] = photo_url

        yield Finding(
            collector=self.name,
            category="sport",
            entity_type="account",
            title=f"AllTrails: {name or username}",
            url=url,
            confidence=0.8,
            payload=payload,
        )
