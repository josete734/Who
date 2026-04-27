"""MapMyRun public profile collector (Wave 2 / A2.5)."""
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

PROFILE_URL = "https://www.mapmyrun.com/profile/{username}/"


def _meta(body: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]+)"',
        body,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


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


@register
class MapMyRunCollector(Collector):
    name = "mapmyrun"
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
        primary_workout: str | None = None
        total_distance: str | None = None

        for entity in _extract_jsonld(body):
            if entity.get("@type") in ("Person", "ProfilePage"):
                name = name or entity.get("name")
                img = entity.get("image")
                if isinstance(img, str):
                    photo_url = photo_url or img
                elif isinstance(img, dict):
                    photo_url = photo_url or img.get("url")

        if not name:
            name = _meta(body, "og:title") or _meta(body, "profile:username")
        if not photo_url:
            photo_url = _meta(body, "og:image")

        m_loc = re.search(
            r'"(?:location|hometown)"\s*:\s*"([^"]{1,120})"', body
        )
        if m_loc:
            location = m_loc.group(1)

        m_dist = re.search(
            r'(?:total[_\s-]*distance|lifetime[_\s-]*distance)[^<\d]{0,40}([\d.,]+\s*(?:km|mi|miles|kilometers))',
            body,
            re.IGNORECASE,
        )
        if m_dist:
            total_distance = m_dist.group(1).strip()

        m_pw = re.search(
            r'(?:primary[_\s-]*workout|favorite[_\s-]*workout|preferred[_\s-]*activity)[^<\w]{0,20}([A-Za-z][A-Za-z\s]{1,30})',
            body,
            re.IGNORECASE,
        )
        if m_pw:
            primary_workout = m_pw.group(1).strip()

        if not name and not total_distance and not primary_workout:
            return

        payload: dict = {
            "platform": "mapmyrun",
            "username": username,
            "name": name,
            "location": location,
            "total_distance": total_distance,
            "primary_workout": primary_workout,
        }
        if photo_url:
            payload["photo_url"] = photo_url

        yield Finding(
            collector=self.name,
            category="sport",
            entity_type="account",
            title=f"MapMyRun: {name or username}",
            url=url,
            confidence=0.75,
            payload=payload,
        )
