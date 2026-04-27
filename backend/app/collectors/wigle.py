"""WiGLE SSID lookup (Wave 4 / A4.1).

Optional collector. Requires ``WIGLE_BASIC`` (base64-encoded ``user:token``)
in settings; if not configured the collector is silently a no-op.

The query is driven by ``extra_context`` containing a ``wigle_ssid=...``
key. WiGLE returns geolocated wireless networks matching the SSID; each
is emitted as a ``location`` finding.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.netfetch import get_client
from app.schemas import SearchInput


WIGLE_URL = "https://api.wigle.net/api/v2/network/search"


def _extract_ssid(extra_context: str | None) -> str | None:
    if not extra_context:
        return None
    m = re.search(r"wigle_ssid\s*[=:]\s*([^\s,;]+)", extra_context)
    return m.group(1).strip() if m else None


@register
class WigleCollector(Collector):
    name = "wigle"
    category = "geo"
    needs = ("extra_context",)
    timeout_seconds = 30
    description = "WiGLE SSID -> geolocated wireless networks (optional, needs WIGLE_BASIC)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        settings = get_settings()
        if not settings.wigle_basic:
            return
        ssid = _extract_ssid(input.extra_context)
        if not ssid:
            return

        headers = {
            "Authorization": f"Basic {settings.wigle_basic}",
            "Accept": "application/json",
        }
        client = await get_client("gentle")
        try:
            try:
                r = await client.get(WIGLE_URL, params={"ssid": ssid}, headers=headers)
            except httpx.HTTPError:
                return
        finally:
            await client.aclose()

        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return

        results = data.get("results") or []
        if not isinstance(results, list):
            return

        for item in results[:50]:
            if not isinstance(item, dict):
                continue
            lat = item.get("trilat")
            lon = item.get("trilong")
            if lat is None or lon is None:
                continue
            yield Finding(
                collector=self.name,
                category="geo",
                entity_type="location",
                title=f"WiGLE SSID match: {item.get('ssid') or ssid}",
                url=None,
                confidence=0.6,
                payload={
                    "platform": "wigle",
                    "ssid": item.get("ssid"),
                    "netid": item.get("netid"),
                    "encryption": item.get("encryption"),
                    "lat": lat,
                    "lon": lon,
                    "country": item.get("country"),
                    "region": item.get("region"),
                    "city": item.get("city"),
                    "lastupdt": item.get("lastupdt"),
                },
            )


__all__ = ["WigleCollector", "WIGLE_URL"]
