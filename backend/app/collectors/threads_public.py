"""Threads (threads.net) public profile scraper.

Best-effort HTML scrape of https://www.threads.net/@<username>. Threads ships a
heavily client-rendered SPA, so we extract whatever JSON-LD / meta / inline
state we can find. Falls back to Tor when we hit a 429 from the gentle policy.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace(".", "")
    s = re.sub(r"[^\d]", "", s)
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_threads_html(html: str) -> dict[str, Any]:
    """Extract whatever profile signals we can from the rendered HTML."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"name": None, "bio": None, "follower_count": None, "posts": []}

    # JSON-LD blocks
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("ProfilePage", "Person"):
                main = item.get("mainEntity") or item
                if isinstance(main, dict):
                    out["name"] = out["name"] or main.get("name") or main.get("alternateName")
                    out["bio"] = out["bio"] or main.get("description")
                    ic = main.get("interactionStatistic")
                    if isinstance(ic, list):
                        for stat in ic:
                            if isinstance(stat, dict) and "Follow" in str(stat.get("interactionType", "")):
                                out["follower_count"] = _to_int(stat.get("userInteractionCount"))

    # Meta tags fallback
    if not out["name"]:
        m = soup.find("meta", attrs={"property": "og:title"})
        if m and m.get("content"):
            out["name"] = m["content"].split(" (")[0]
    if not out["bio"]:
        m = soup.find("meta", attrs={"property": "og:description"})
        if m and m.get("content"):
            out["bio"] = m["content"]

    # Follower count via text heuristic
    if out["follower_count"] is None:
        m = re.search(r'([\d,.]+)\s*[Ff]ollowers', html)
        if m:
            out["follower_count"] = _to_int(m.group(1))

    # Visible post bodies — Threads embeds them in spans within article elements.
    for art in soup.find_all("article"):
        text = art.get_text(" ", strip=True)
        if text:
            out["posts"].append(text[:500])
    if not out["posts"]:
        # Fallback: og:description repeated for each visible thread div
        for div in soup.select("div[data-pressable-container='true']"):
            text = div.get_text(" ", strip=True)
            if text:
                out["posts"].append(text[:500])

    return out


@register
class ThreadsPublicCollector(Collector):
    name = "threads_public"
    category = "social"
    needs = ("username",)
    timeout_seconds = 45
    description = "Threads.net public profile HTML scrape (gentle, Tor fallback on 429)"

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        url = f"https://www.threads.net/@{u}"

        html: str | None = None
        gentle = await get_client("gentle")
        try:
            try:
                r = await gentle.get(url)
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                html = r.text
            elif r is not None and r.status_code == 429:
                tor = await get_client("tor")
                try:
                    try:
                        r2 = await tor.get(url)
                    except httpx.HTTPError:
                        r2 = None
                    if r2 is not None and r2.status_code == 200:
                        html = r2.text
                finally:
                    await tor.aclose()
        finally:
            await gentle.aclose()

        if not html:
            return

        data = parse_threads_html(html)
        yield Finding(
            collector=self.name,
            category="username",
            entity_type="ThreadsProfile",
            title=f"Threads: @{u}",
            url=url,
            confidence=0.75,
            payload={
                "username": u,
                "name": data.get("name"),
                "bio": data.get("bio"),
                "follower_count": data.get("follower_count"),
                "post_count_visible": len(data.get("posts") or []),
                "posts": (data.get("posts") or [])[:25],
            },
        )


__all__ = ["ThreadsPublicCollector", "parse_threads_html"]
