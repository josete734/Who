"""Combot.org Telegram channel/group statistics scrape (Wave 4 / A4.2)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


PROFILE_URL = "https://combot.org/telegram/{channel}"


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


def _parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {
        "title": None,
        "description": None,
        "members": None,
        "messages_per_day": None,
    }
    title = soup.find("meta", attrs={"property": "og:title"})
    if title and title.get("content"):
        out["title"] = title["content"].strip()
    desc = soup.find("meta", attrs={"property": "og:description"})
    if desc and desc.get("content"):
        out["description"] = desc["content"].strip()

    m_members = re.search(r"([\d\s.,]+)\s*(members|участник)", html, re.IGNORECASE)
    if m_members:
        out["members"] = _to_int(m_members.group(1))

    m_msg = re.search(r"([\d\s.,]+)\s*messages? per day", html, re.IGNORECASE)
    if m_msg:
        out["messages_per_day"] = _to_int(m_msg.group(1))

    return out


@register
class CombotCollector(Collector):
    name = "combot"
    category = "messengers"
    needs = ("username",)
    timeout_seconds = 25
    description = "Combot.org Telegram channel/group HTML scrape."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        channel = input.username.lstrip("@")
        url = PROFILE_URL.format(channel=channel)

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

        data = _parse(r.text or "")
        if not data.get("title") and data.get("members") is None:
            return

        yield Finding(
            collector=self.name,
            category=self.category,
            entity_type="channel",
            title=f"Combot: {data.get('title') or channel}",
            url=url,
            confidence=0.7,
            payload={
                "platform": "telegram",
                "source": "combot",
                "channel": channel,
                **data,
            },
        )


__all__ = ["CombotCollector", "PROFILE_URL", "_parse"]
