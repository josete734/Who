"""YouTube channel discovery via oEmbed + Atom RSS (Wave 8).

Uses two unauthenticated, key-less endpoints:

* ``https://www.youtube.com/oembed?url=...`` — official oEmbed endpoint that
  returns ``author_name``, ``author_url``, ``thumbnail_url``, ``title`` for
  any public channel/video URL. No quota, no key.
* ``https://www.youtube.com/feeds/videos.xml?channel_handle=@<handle>`` —
  Atom feed of the channel's recent videos with full timestamps. Stable
  contract from YouTube.

Inputs: ``username`` (treated as a channel handle).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from xml.etree import ElementTree as ET

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

_OEMBED = "https://www.youtube.com/oembed"
_FEED = "https://www.youtube.com/feeds/videos.xml"


@register
class YouTubeChannelCollector(Collector):
    name = "youtube"
    category = "social"
    needs = ("username",)
    timeout_seconds = 25
    description = "YouTube channel via oEmbed + Atom RSS feed (no key required)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        channel_url = f"https://www.youtube.com/@{u}"

        async with client(timeout=15) as c:
            # 1) oEmbed for the canonical channel name + thumbnail.
            try:
                r = await c.get(_OEMBED, params={"url": channel_url, "format": "json"})
            except httpx.HTTPError:
                r = None
            channel_name: str | None = None
            thumbnail: str | None = None
            if r is not None and r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                channel_name = data.get("author_name") or data.get("title")
                thumbnail = data.get("thumbnail_url")
                if channel_name:
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="YouTubeChannel",
                        title=f"YouTube: @{u} ({channel_name})",
                        url=channel_url,
                        confidence=0.9,
                        payload={
                            "username": u,
                            "name": channel_name,
                            "thumbnail": thumbnail,
                            "source": "oembed",
                        },
                    )

            # 2) Atom feed for recent videos. Even a brand-new channel returns
            # an empty feed (200 + <feed/>). 404 = handle doesn't exist.
            try:
                r2 = await c.get(_FEED, params={"channel_handle": f"@{u}"})
            except httpx.HTTPError:
                return
            if r2.status_code != 200 or not r2.text:
                return
            try:
                root = ET.fromstring(r2.text)
            except ET.ParseError:
                return

            ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
            videos: list[dict] = []
            for entry in root.findall("a:entry", ns)[:15]:
                title = (entry.findtext("a:title", default="", namespaces=ns) or "")[:200]
                vid = entry.findtext("yt:videoId", default="", namespaces=ns) or ""
                published = entry.findtext("a:published", default="", namespaces=ns) or ""
                videos.append(
                    {"video_id": vid, "title": title, "published": published}
                )

            if videos:
                yield Finding(
                    collector=self.name,
                    category="social",
                    entity_type="YouTubeRecentVideos",
                    title=f"YouTube @{u}: {len(videos)} vídeos recientes",
                    url=channel_url,
                    confidence=0.85,
                    payload={
                        "username": u,
                        "videos": videos,
                        "source": "atom_rss",
                    },
                )
