"""Suunto public app profile collector (Wave 2 / A2.5).

WARNING: high breakage risk — Suunto changes the SPA shell often, so
we wrap every parse step in try/except and emit a finding even with
partial data.
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

PROFILE_URL = "https://app.suunto.com/{username}"


def _meta(body: str, prop: str) -> str | None:
    try:
        m = re.search(
            rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]+)"',
            body,
            re.IGNORECASE,
        )
        return m.group(1).strip() if m else None
    except re.error:
        return None


@register
class SuuntoPublicCollector(Collector):
    name = "suunto_public"
    category = "sport"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        username = input.username.lstrip("@")
        url = PROFILE_URL.format(username=username)

        try:
            async with await get_client("gentle") as c:
                r = await c.get(url, timeout=12.0)
        except (httpx.HTTPError, OSError):
            return

        if r.status_code != 200:
            return

        body = r.text or ""
        name: str | None = None
        photo_url: str | None = None
        recent_workouts: list[dict] = []
        polyline: str | None = None
        lat: float | None = None
        lon: float | None = None

        try:
            name = _meta(body, "og:title")
            photo_url = _meta(body, "og:image")
        except Exception:  # pragma: no cover - defensive
            logger.debug("suunto: meta parse failed", exc_info=True)

        try:
            for m in re.finditer(
                r'<script[^>]+(?:id="__NEXT_DATA__"|type="application/json")[^>]*>(.*?)</script>',
                body,
                re.DOTALL | re.IGNORECASE,
            ):
                try:
                    data = json.loads(m.group(1).strip())
                except (ValueError, json.JSONDecodeError):
                    continue
                stack: list = [data]
                while stack and len(recent_workouts) < 10:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        if name is None:
                            name = (
                                cur.get("displayName")
                                or cur.get("fullName")
                                or cur.get("nickname")
                                or name
                            )
                        if photo_url is None:
                            photo_url = (
                                cur.get("profilePicture")
                                or cur.get("avatarUrl")
                                or photo_url
                            )
                        if polyline is None:
                            polyline = (
                                cur.get("polyline") or cur.get("encodedPolyline") or polyline
                            )
                        if lat is None and isinstance(cur.get("lat"), (int, float)):
                            lat = float(cur["lat"])
                            lon_v = cur.get("lon") or cur.get("lng")
                            if isinstance(lon_v, (int, float)):
                                lon = float(lon_v)
                        if cur.get("activityType") or cur.get("workoutType"):
                            recent_workouts.append(
                                {
                                    "type": cur.get("activityType")
                                    or cur.get("workoutType"),
                                    "date": cur.get("startTime")
                                    or cur.get("startDate"),
                                    "distance": cur.get("totalDistance")
                                    or cur.get("distance"),
                                }
                            )
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
        except Exception:  # pragma: no cover - defensive
            logger.debug("suunto: json scan failed", exc_info=True)

        if not name and not recent_workouts and not photo_url:
            return

        payload: dict = {
            "platform": "suunto",
            "username": username,
            "name": name,
            "recent_workouts": recent_workouts,
        }
        if photo_url:
            payload["photo_url"] = photo_url
        if polyline:
            payload["polyline"] = polyline
        if lat is not None and lon is not None:
            payload["lat"] = lat
            payload["lon"] = lon

        yield Finding(
            collector=self.name,
            category="sport",
            entity_type="account",
            title=f"Suunto: {name or username}",
            url=url,
            confidence=0.6,
            payload=payload,
        )
