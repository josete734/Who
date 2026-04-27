"""Garmin Connect public profile collector (Wave 2 / A2.5).

Scrapes the public profile page (HTML) and the userprofile-service JSON
endpoint, both reachable without auth for users who opted into public mode.
"""
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

PROFILE_URL = "https://connect.garmin.com/modern/profile/{username}"
SOCIAL_JSON_URL = (
    "https://connect.garmin.com/proxy/userprofile-service/socialProfile/{username}"
)


@register
class GarminConnectPublicCollector(Collector):
    name = "garmin_connect_public"
    category = "sport"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        username = input.username.lstrip("@")
        display_name: str | None = None
        location: str | None = None
        photo_url: str | None = None
        badges: list[str] = []
        recent_activities_count: int | None = None
        url = PROFILE_URL.format(username=username)

        async with await get_client("gentle") as c:
            try:
                rj = await c.get(SOCIAL_JSON_URL.format(username=username), timeout=12.0)
            except (httpx.HTTPError, OSError):
                rj = None
            if rj is not None and rj.status_code == 200:
                try:
                    data = rj.json()
                except (ValueError, json.JSONDecodeError):
                    data = {}
                if isinstance(data, dict):
                    display_name = (
                        data.get("displayName")
                        or data.get("fullName")
                        or data.get("userName")
                    )
                    location = data.get("location")
                    photo_url = data.get("profileImageUrlLarge") or data.get(
                        "profileImageUrlMedium"
                    )
                    raw_badges = data.get("userRoles") or data.get("badges") or []
                    if isinstance(raw_badges, list):
                        badges = [str(b) for b in raw_badges if b]

            try:
                rh = await c.get(url, timeout=12.0)
            except (httpx.HTTPError, OSError):
                rh = None
            if rh is not None and rh.status_code == 200:
                body = rh.text or ""
                if not display_name:
                    m = re.search(r"<title>([^<]+)</title>", body, re.IGNORECASE)
                    if m:
                        display_name = m.group(1).strip()
                m_act = re.search(
                    r'recent[_\s-]*activit[^\d]{0,40}(\d{1,4})', body, re.IGNORECASE
                )
                if m_act:
                    try:
                        recent_activities_count = int(m_act.group(1))
                    except ValueError:
                        recent_activities_count = None

        if not display_name and recent_activities_count is None and not photo_url:
            return

        payload: dict = {
            "platform": "garmin_connect",
            "username": username,
            "display_name": display_name,
            "location": location,
            "badges": badges,
            "recent_activities_count": recent_activities_count,
        }
        if photo_url:
            payload["photo_url"] = photo_url

        yield Finding(
            collector=self.name,
            category="sport",
            entity_type="account",
            title=f"Garmin Connect: {display_name or username}",
            url=url,
            confidence=0.75,
            payload=payload,
        )
