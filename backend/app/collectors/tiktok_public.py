"""TikTok public profile existence.

Only emits a Finding if we can extract an embedded SIGI_STATE / __UNIVERSAL_DATA
JSON blob with a uniqueId that **equals** the requested handle (case-insensitive).
This avoids false positives when TikTok returns a generic SPA shell / redirect
for missing profiles or when Cloudflare blocks us.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

# Regex to locate the embedded UNIVERSAL_DATA_FOR_REHYDRATION script (current TikTok layout).
_UNIVERSAL_RX = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
# Fallback SIGI_STATE (older layout).
_SIGI_RX = re.compile(r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>', re.DOTALL)


def _extract_profile(html: str) -> dict | None:
    for rx in (_UNIVERSAL_RX, _SIGI_RX):
        m = rx.search(html)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        # Walk a few known paths
        candidates = [
            data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {}),
            data.get("UserModule", {}).get("users", {}),
        ]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            u = c.get("user") if "user" in c else next(iter(c.values()), {})
            if isinstance(u, dict) and u.get("uniqueId"):
                return u
    return None


@register
class TikTokPublicCollector(Collector):
    name = "tiktok_public"
    category = "social"
    needs = ("username",)
    timeout_seconds = 15
    description = "TikTok profile existence (strict uniqueId match, no false positives)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        url = f"https://www.tiktok.com/@{u}"
        async with client(timeout=10) as c:
            try:
                r = await c.get(url, headers={"Accept": "text/html"})
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        profile = _extract_profile(r.text)
        if not profile:
            return
        unique_id = str(profile.get("uniqueId") or "").lower()
        if unique_id != u.lower():
            # TikTok served a different profile (homonym or redirect). DO NOT report as match.
            return

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="TikTokProfile",
            title=f"TikTok: @{profile.get('uniqueId')} ({profile.get('nickname') or ''})",
            url=url,
            confidence=0.85,
            payload={
                "uniqueId": profile.get("uniqueId"),
                "nickname": profile.get("nickname"),
                "signature": profile.get("signature"),
                "verified": profile.get("verified"),
                "secUid": profile.get("secUid"),
                "id": profile.get("id"),
                "region": profile.get("region"),
                "language": profile.get("language"),
                "ftc": profile.get("ftc"),
                "followerCount": (profile.get("stats") or {}).get("followerCount"),
                "followingCount": (profile.get("stats") or {}).get("followingCount"),
                "heartCount": (profile.get("stats") or {}).get("heartCount"),
                "videoCount": (profile.get("stats") or {}).get("videoCount"),
                "note": "Match por uniqueId exacto. Verifica que sea realmente el sujeto (no solo el alias).",
            },
        )
