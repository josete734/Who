"""TGStat public Telegram channel scrape (Wave 4 / A4.2)."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


PROFILE_URL = "https://tgstat.com/channel/@{channel}"


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
        "subscribers": None,
        "category": None,
        "language": None,
    }
    title = soup.find("meta", attrs={"property": "og:title"})
    if title and title.get("content"):
        out["title"] = title["content"].strip()
    desc = soup.find("meta", attrs={"property": "og:description"})
    if desc and desc.get("content"):
        out["description"] = desc["content"].strip()

    # Subscribers: TGStat tends to render in a "channel-stat" block.
    m = re.search(r"([\d\s.,]+)\s*(subscribers|подписчик)", html, re.IGNORECASE)
    if m:
        out["subscribers"] = _to_int(m.group(1))

    for h in soup.select(".channel-info, .profile-row, h2, h3"):
        text = h.get_text(" ", strip=True)
        m_lang = re.search(r"\b(English|Russian|Spanish|Español|Русский)\b", text, re.IGNORECASE)
        if m_lang and not out["language"]:
            out["language"] = m_lang.group(1)
    return out


@register
class TGStatCollector(Collector):
    name = "tgstat"
    category = "messengers"
    needs = ("username",)
    timeout_seconds = 25
    description = "TGStat public Telegram channel HTML scrape."

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
        if not data.get("title") and data.get("subscribers") is None:
            return

        yield Finding(
            collector=self.name,
            category=self.category,
            entity_type="channel",
            title=f"TGStat: {data.get('title') or channel}",
            url=url,
            confidence=0.7,
            payload={
                "platform": "telegram",
                "source": "tgstat",
                "channel": channel,
                **data,
            },
        )


__all__ = ["TGStatCollector", "PROFILE_URL", "_parse"]
