"""Disboard.org public Discord server search (Wave 4 / A4.2)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


SEARCH_URL = "https://disboard.org/search"


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # Disboard server cards live under "/servers/<id>" anchors.
    for card in soup.select('a[href^="/servers/"], a[href^="/server/"]'):
        href = card.get("href") or ""
        m = re.match(r"^/servers?/(\d+|[A-Za-z0-9_-]+)", href)
        if not m:
            continue
        server_id = m.group(1)
        name = card.get_text(" ", strip=True)[:120] or None

        parent = card
        members = None
        for _ in range(3):
            parent = getattr(parent, "parent", None)
            if parent is None:
                break
            text = parent.get_text(" ", strip=True) if hasattr(parent, "get_text") else ""
            m_mem = re.search(r"([\d.,]+)\s*(members|online)", text, re.IGNORECASE)
            if m_mem:
                members = _to_int(m_mem.group(1))
                break

        out.append(
            {
                "server_id": server_id,
                "name": name,
                "members": members,
                "url": f"https://disboard.org{href}",
            }
        )
        if len(out) >= 30:
            break
    return out


@register
class DisboardCollector(Collector):
    name = "disboard"
    category = "messengers"
    needs = ("username", "full_name", "extra_context")
    timeout_seconds = 25
    description = "Disboard.org Discord server search (HTML scrape)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        q = input.username or input.full_name or input.extra_context
        if not q:
            return
        keyword = q.lstrip("@").strip()
        if not keyword:
            return

        client = await get_client("gentle")
        try:
            try:
                r = await client.get(SEARCH_URL, params={"keyword": keyword})
            except httpx.HTTPError:
                return
        finally:
            await client.aclose()

        if r.status_code != 200:
            return

        for srv in _parse(r.text or ""):
            yield Finding(
                collector=self.name,
                category=self.category,
                entity_type="discord_server",
                title=f"Disboard: {srv.get('name') or srv.get('server_id')}",
                url=srv.get("url"),
                confidence=0.55,
                payload={
                    "platform": "discord",
                    "source": "disboard",
                    "query": keyword,
                    **srv,
                },
            )


__all__ = ["DisboardCollector", "SEARCH_URL", "_parse"]
