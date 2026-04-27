"""Twitter/X collector via public Nitter mirrors.

Nitter is a privacy-friendly front-end for Twitter that exposes both an HTML
profile page and an RSS feed. Because individual instances frequently rate-limit
or go offline, this collector iterates through a configurable list and uses the
first mirror that returns a usable response (round-robin with first-success).

Inputs: ``username``.
Findings:
  * profile_meta — bio, location, joined date, follower count
  * tweets — list of recent tweets (text + ts + url)
  * external_links_in_bio — URLs extracted from the bio for pivoting

The collector is NOT auto-registered — flip the import in
``app/collectors/__init__.py`` once it has been validated in production.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.netfetch import get_client
from app.schemas import SearchInput

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

DEFAULT_INSTANCES = (
    "nitter.net",
    "nitter.privacydev.net",
    "nitter.poast.org",
)

_RETRY_STATUS = {429, 500, 502, 503, 504}

_URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)


def _instances_from_env() -> list[str]:
    raw = os.environ.get("NITTER_INSTANCES", "").strip()
    if not raw:
        return list(DEFAULT_INSTANCES)
    items = [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]
    return items or list(DEFAULT_INSTANCES)


def _strip_host(value: str) -> str:
    return value.replace("https://", "").replace("http://", "").rstrip("/")


def _pick(html: str, pat: str) -> str | None:
    m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _parse_profile(html: str, username: str, instance: str) -> dict[str, Any]:
    """Extract profile metadata from a Nitter profile HTML page."""
    bio = _pick(html, r'<div class="profile-bio">\s*<p[^>]*>(.*?)</p>')
    if bio:
        # Strip HTML tags for a clean string but keep raw for link extraction.
        bio_text = re.sub(r"<[^>]+>", "", bio).strip()
    else:
        bio_text = None

    location = _pick(html, r'<div class="profile-location">.*?<span>(.*?)</span>')
    joined = _pick(html, r'<div class="profile-joindate"[^>]*>\s*<span title="([^"]+)"')
    if not joined:
        joined = _pick(html, r'<div class="profile-joindate"[^>]*>(.*?)</div>')
        if joined:
            joined = re.sub(r"<[^>]+>", "", joined).strip()

    # Stat blocks: followers / following / tweets
    followers = None
    following = None
    tweets_count = None
    for m in re.finditer(
        r'<li class="(followers|following|posts)">.*?<span class="profile-stat-num">([^<]+)</span>',
        html,
        re.DOTALL,
    ):
        kind, num = m.group(1), m.group(2)
        n = _parse_int(num)
        if kind == "followers":
            followers = n
        elif kind == "following":
            following = n
        elif kind == "posts":
            tweets_count = n

    full_name = _pick(html, r'<a class="profile-card-fullname"[^>]*>([^<]+)</a>')

    external_links: list[str] = []
    if bio:
        external_links.extend(_URL_RE.findall(bio))
    website = _pick(html, r'<div class="profile-website">.*?<a href="([^"]+)"')
    if website and website not in external_links:
        external_links.append(website)

    return {
        "username": username,
        "full_name": full_name,
        "bio": bio_text,
        "location": location,
        "joined": joined,
        "followers": followers,
        "following": following,
        "tweets_count": tweets_count,
        "external_links": external_links,
        "source_instance": instance,
    }


def _parse_rss(content: bytes, username: str, limit: int = 20) -> list[dict[str, Any]]:
    feed = feedparser.parse(content)
    out: list[dict[str, Any]] = []
    for entry in feed.entries[:limit]:
        link = entry.get("link") or ""
        # Rewrite nitter URL back to twitter.com for downstream pivoting.
        canonical = re.sub(r"https?://[^/]+/", "https://twitter.com/", link)
        out.append(
            {
                "text": re.sub(r"<[^>]+>", "", entry.get("title", "")).strip(),
                "ts": entry.get("published") or entry.get("updated"),
                "url": canonical,
                "nitter_url": link,
            }
        )
    return out


SYNDICATION_URL = (
    "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
)
WAYBACK_CDX_URL = (
    "https://web.archive.org/cdx/search/cdx"
    "?url=twitter.com/{username}&output=json&limit=20"
)


async def _fetch_syndication(username: str) -> dict[str, Any] | None:
    """Fallback: parse Twitter syndication timeline-profile HTML for embedded JSON."""
    url = SYNDICATION_URL.format(username=username)
    try:
        c = await get_client("gentle")
    except Exception:
        return None
    try:
        try:
            r = await c.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        html = r.text
    finally:
        await c.aclose()

    payload: dict[str, Any] | None = None
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("script", id="__NEXT_DATA__")
            if tag and tag.string:
                payload = json.loads(tag.string)
        except Exception:
            payload = None

    if payload is None:
        m = re.search(
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
            html,
            re.DOTALL,
        )
        if m:
            try:
                payload = json.loads(m.group(1))
            except Exception:
                payload = None
    if payload is None:
        m = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                payload = json.loads(m.group(1))
            except Exception:
                payload = None

    if not payload:
        return None

    user: dict[str, Any] = {}

    def _walk(obj: Any) -> None:
        if user:
            return
        if isinstance(obj, dict):
            if obj.get("screen_name") and (
                obj.get("name") or obj.get("description") is not None
            ):
                user.update(obj)
                return
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(payload)
    if not user:
        return None

    bio = user.get("description") or user.get("bio")
    external_links: list[str] = []
    if bio:
        external_links.extend(_URL_RE.findall(bio))
    return {
        "username": user.get("screen_name") or username,
        "full_name": user.get("name"),
        "bio": bio,
        "location": user.get("location"),
        "joined": user.get("created_at"),
        "followers": user.get("followers_count"),
        "following": user.get("friends_count"),
        "tweets_count": user.get("statuses_count"),
        "profile_pic_url": user.get("profile_image_url_https") or user.get("profile_image_url"),
        "external_links": external_links,
        "source_instance": "syndication.twitter.com",
    }


async def _fetch_wayback_cdx(username: str) -> list[dict[str, Any]]:
    """Wayback CDX scan for historical bio/profile snapshots of @username."""
    url = WAYBACK_CDX_URL.format(username=username)
    try:
        c = await get_client("gentle")
    except Exception:
        return []
    try:
        try:
            r = await c.get(url)
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except ValueError:
            return []
    finally:
        await c.aclose()
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    header, *rows = data
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(zip(header, row))
        ts = rec.get("timestamp")
        original = rec.get("original")
        if ts and original:
            out.append(
                {
                    "timestamp": ts,
                    "original": original,
                    "snapshot": f"https://web.archive.org/web/{ts}/{original}",
                    "status": rec.get("statuscode"),
                    "digest": rec.get("digest"),
                }
            )
    return out


@register
class TwitterNitterCollector(Collector):
    name = "twitter_nitter"
    category = "social"
    needs = ("username",)
    timeout_seconds = 40
    description = "Twitter/X profile + recent tweets via Nitter mirrors."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        u = input.username.lstrip("@")
        instances = [_strip_host(i) for i in _instances_from_env()]

        profile_meta: dict[str, Any] | None = None
        tweets: list[dict[str, Any]] = []
        used_instance: str | None = None

        async with client(timeout=12) as c:
            for inst in instances:
                base = f"https://{inst}"
                try:
                    pr = await c.get(urljoin(base, f"/{u}"))
                except httpx.HTTPError:
                    continue
                if pr.status_code in _RETRY_STATUS:
                    continue
                if pr.status_code != 200:
                    continue
                html = pr.text
                if "User not found" in html or 'class="error-panel"' in html:
                    # Definitive negative response — no point trying other mirrors.
                    return
                profile_meta = _parse_profile(html, u, inst)

                try:
                    rr = await c.get(urljoin(base, f"/{u}/rss"))
                except httpx.HTTPError:
                    rr = None
                if rr is not None and rr.status_code == 200:
                    tweets = _parse_rss(rr.content, u, limit=20)
                elif rr is not None and rr.status_code in _RETRY_STATUS:
                    # Profile worked but RSS rate-limited — try next mirror for RSS.
                    for alt in instances:
                        if alt == inst:
                            continue
                        try:
                            rr2 = await c.get(urljoin(f"https://{alt}", f"/{u}/rss"))
                        except httpx.HTTPError:
                            continue
                        if rr2.status_code == 200:
                            tweets = _parse_rss(rr2.content, u, limit=20)
                            break
                used_instance = inst
                break

        confidence = 0.8
        # Fallback: if no Nitter mirror worked, try Twitter syndication endpoint.
        if profile_meta is None:
            profile_meta = await _fetch_syndication(u)
            if profile_meta is not None:
                used_instance = profile_meta.get("source_instance")
                confidence = 0.75

        if profile_meta is None:
            # Last resort: still emit Wayback historical snapshots if any,
            # so the case has *some* signal.
            history = await _fetch_wayback_cdx(u)
            for snap in history:
                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="TwitterHistoricalSnapshot",
                    title=f"Wayback @{u} snapshot {snap['timestamp']}",
                    url=snap["snapshot"],
                    confidence=0.5,
                    payload={"username": u, **snap},
                )
            return

        canonical_url = f"https://twitter.com/{u}"
        title_name = profile_meta.get("full_name") or u

        # Wayback historical bio drift (always best-effort).
        history = await _fetch_wayback_cdx(u)

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="TwitterProfile",
            title=f"Twitter/X: @{u} ({title_name})",
            url=canonical_url,
            confidence=confidence,
            payload={
                "profile_meta": profile_meta,
                "tweets": tweets,
                "external_links_in_bio": profile_meta.get("external_links", []),
                "source_instance": used_instance,
                "wayback_snapshots": history,
            },
        )

        for link in profile_meta.get("external_links", []) or []:
            yield Finding(
                collector=self.name,
                category="username",
                entity_type="ExternalLink",
                title=f"Bio link from @{u}: {link}",
                url=link,
                confidence=0.65,
                payload={"source": "twitter_bio", "username": u},
            )

        for snap in history:
            yield Finding(
                collector=self.name,
                category="username",
                entity_type="TwitterHistoricalSnapshot",
                title=f"Wayback @{u} snapshot {snap['timestamp']}",
                url=snap["snapshot"],
                confidence=0.5,
                payload={"username": u, **snap},
            )
