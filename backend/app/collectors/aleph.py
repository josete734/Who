"""Aleph (OCCRP) entity search collector — OPTIONAL, requires free API key.

Searches https://aleph.occrp.org/ for Person entities matching the subject's
full_name. Aleph aggregates leaks, sanctions lists, court records, corporate
registries, etc. — high-signal for journalist-grade investigations.

Auth: header ``Authorization: ApiKey {settings.aleph_free_key}``. If the key
is unset the collector silently emits nothing (opt-in).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

ALEPH_URL = "https://aleph.occrp.org/api/2/entities"
_LIMIT = 20


@register
class AlephCollector(Collector):
    name = "aleph"
    category = "leaks"
    needs = ("full_name",)
    timeout_seconds = 25
    description = "OCCRP Aleph: leaks, sanctions, registries (free API key)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        key = (get_settings().aleph_free_key or "").strip()
        if not key:
            return
        if not input.full_name:
            return

        headers = {"Authorization": f"ApiKey {key}", "Accept": "application/json"}
        params = {
            "q": input.full_name,
            "filter:schema": "Person",
            "limit": _LIMIT,
        }

        try:
            async with await get_client("gentle") as c:
                r = await c.get(ALEPH_URL, params=params, headers=headers, timeout=20.0)
        except (httpx.HTTPError, OSError):
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return

        results = data.get("results") or []
        if not isinstance(results, list):
            return

        for entry in results:
            if not isinstance(entry, dict):
                continue
            f = _entry_to_finding(self.name, entry)
            if f is not None:
                yield f


def _entry_to_finding(collector_name: str, entry: dict[str, Any]) -> Finding | None:
    props = entry.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    names = props.get("name") or []
    name = names[0] if isinstance(names, list) and names else entry.get("caption") or "unknown"

    datasets = entry.get("datasets") or []
    collection = entry.get("collection") or {}
    collection_label = (
        collection.get("label") if isinstance(collection, dict) else None
    ) or ""

    dates = []
    for k in ("birthDate", "deathDate", "modifiedAt", "createdAt"):
        v = props.get(k) if k in props else entry.get(k)
        if isinstance(v, list) and v:
            dates.append({k: v[0]})
        elif isinstance(v, str):
            dates.append({k: v})

    related = []
    schemata = entry.get("schemata") or []
    for link in (entry.get("links") or [])[:10]:
        if isinstance(link, dict):
            related.append(
                {
                    "name": link.get("name") or link.get("caption"),
                    "schema": link.get("schema"),
                }
            )

    countries = props.get("country") or props.get("nationality") or []
    if isinstance(countries, str):
        countries = [countries]

    entity_id = entry.get("id") or ""
    url = f"https://aleph.occrp.org/entities/{entity_id}" if entity_id else None

    return Finding(
        collector=collector_name,
        category="leaks",
        entity_type="document",
        title=f"Aleph: {name} ({collection_label})" if collection_label else f"Aleph: {name}",
        url=url,
        confidence=0.75,
        payload={
            "value": name,
            "collection": collection_label,
            "datasets": datasets,
            "schemata": schemata,
            "countries": countries,
            "dates": dates,
            "related": related,
            "aleph_id": entity_id,
        },
    )
