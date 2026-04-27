"""Polar Flow public athlete profile collector (Wave 2 / A2.5)."""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

PROFILE_URL = "https://flow.polar.com/athlete/{username}"


def _meta(body: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]+)"',
        body,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


@register
class PolarFlowPublicCollector(Collector):
    name = "polar_flow_public"
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

        name = _meta(body, "og:title")
        if not name:
            m = re.search(
                r'<h1[^>]*class="[^"]*(?:athlete|profile)[^"]*"[^>]*>([^<]+)</h1>',
                body,
                re.IGNORECASE,
            )
            name = m.group(1).strip() if m else None
        photo_url = _meta(body, "og:image")
        club = None
        m_club = re.search(
            r'(?:class="[^"]*club[^"]*"[^>]*>|"club"\s*:\s*")([^<"]{1,120})',
            body,
            re.IGNORECASE,
        )
        if m_club:
            club = m_club.group(1).strip()

        recent_activities: list[dict] = []
        for m_act in re.finditer(
            r'<(?:li|div|article)[^>]+class="[^"]*(?:activity|training-session)[^"]*"[^>]*>(.*?)</(?:li|div|article)>',
            body,
            re.DOTALL | re.IGNORECASE,
        ):
            chunk = m_act.group(1)
            sport_m = re.search(
                r'(?:sport|activity-type)[^<>]*?>([^<]{1,40})<', chunk, re.IGNORECASE
            )
            date_m = re.search(
                r'(20\d{2}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})', chunk
            )
            if sport_m or date_m:
                recent_activities.append(
                    {
                        "sport": sport_m.group(1).strip() if sport_m else None,
                        "date": date_m.group(1).strip() if date_m else None,
                    }
                )
            if len(recent_activities) >= 10:
                break

        if not name and not recent_activities and not club:
            return

        payload: dict = {
            "platform": "polar_flow",
            "username": username,
            "name": name,
            "club": club,
            "recent_activities": recent_activities,
        }
        if photo_url:
            payload["photo_url"] = photo_url

        yield Finding(
            collector=self.name,
            category="sport",
            entity_type="account",
            title=f"Polar Flow: {name or username}",
            url=url,
            confidence=0.7,
            payload=payload,
        )
