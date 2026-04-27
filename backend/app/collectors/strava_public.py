"""Strava public profile collector (Wave 1 / A1.1).

Resolves an athlete via (in priority order):
  1. ``strava_athlete_id=<digits>`` token in ``SearchInput.extra_context``.
  2. The username slug — ``https://www.strava.com/athletes/{username}`` follows
     redirects to the canonical numeric profile URL; we also parse the HTML
     ``<meta property="og:url">`` / JSON-LD as fallback.
  3. A SearXNG dork ``site:strava.com/athletes "{full_name}"`` and we extract
     the athlete id from the first matching URL.

Once an athlete id is known we fetch:
  * ``/athletes/{id}``        → profile (name, photo, hometown, follower
                                counts, club ids, recent activities w/
                                optional encoded polylines).
  * ``/athletes/{id}/routes`` → public saved routes (name, distance, type).

All HTTP goes through :func:`app.netfetch.get_client('gentle')`. Failures
(404, login wall, network errors) yield no findings — the collector is
intentionally tolerant so it can never abort a case.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.netfetch import get_client
from app.schemas import SearchInput

logger = logging.getLogger(__name__)

PROFILE_URL = "https://www.strava.com/athletes/{slug}"
ROUTES_URL = "https://www.strava.com/athletes/{athlete_id}/routes"

_RE_ATHLETE_ID = re.compile(r"strava\.com/athletes/(\d+)", re.IGNORECASE)
_RE_CTX_ID = re.compile(r"strava_athlete_id\s*[=:]\s*(\d+)")
_RE_DIGITS = re.compile(r"^\d+$")


def _athlete_id_from_extra(ctx: str | None) -> str | None:
    if not ctx:
        return None
    m = _RE_CTX_ID.search(ctx)
    return m.group(1) if m else None


def _athlete_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _RE_ATHLETE_ID.search(url)
    return m.group(1) if m else None


def _athlete_id_from_html(html: str) -> str | None:
    """Look in <meta property="og:url"> first, then any JSON-LD blob, then any
    occurrence of ``/athletes/<digits>`` in the body.
    """
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover — lxml always installed
        soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", attrs={"property": "og:url"})
    if og is not None:
        aid = _athlete_id_from_url(og.get("content"))
        if aid:
            return aid

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "{}")
        except (ValueError, TypeError):
            continue
        candidates: list[Any] = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            for key in ("@id", "url", "mainEntityOfPage"):
                aid = _athlete_id_from_url(str(entry.get(key) or ""))
                if aid:
                    return aid

    return _athlete_id_from_url(html)


def _parse_profile(html: str) -> dict[str, Any]:
    """Extract the structured fields we care about from the profile HTML."""
    out: dict[str, Any] = {
        "display_name": None,
        "photo_url": None,
        "hometown": None,
        "city": None,
        "follower_count": None,
        "follow_count": None,
        "club_ids": [],
        "recent_activities": [],
    }
    if not html:
        return out
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        out["display_name"] = og_title["content"].strip()
    if not out["display_name"]:
        h1 = soup.find(["h1", "h2"])
        if h1 and h1.get_text(strip=True):
            out["display_name"] = h1.get_text(strip=True)

    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        out["photo_url"] = og_image["content"]

    # Hometown / city: look for explicit <div class="location"> or labeled text.
    loc_node = soup.find(class_=re.compile(r"location", re.I))
    if loc_node and loc_node.get_text(strip=True):
        out["hometown"] = loc_node.get_text(" ", strip=True)
        out["city"] = out["hometown"].split(",")[0].strip() or None

    # Follower / follow counts in <a href="/athletes/{id}/followers">123</a>
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True)
        m_count = re.search(r"(\d[\d,\.]*)", txt or "")
        n = int(m_count.group(1).replace(",", "").replace(".", "")) if m_count else None
        if "/followers" in href and n is not None and out["follower_count"] is None:
            out["follower_count"] = n
        elif "/following" in href and n is not None and out["follow_count"] is None:
            out["follow_count"] = n
        elif "/clubs/" in href:
            mc = re.search(r"/clubs/(\d+)", href)
            if mc:
                cid = mc.group(1)
                if cid not in out["club_ids"]:
                    out["club_ids"].append(cid)

    # Recent activities table — Strava uses <table class="..."> with rows that
    # often embed a data attribute holding the encoded polyline. We only grab
    # what is visible without auth.
    for row in soup.select("table tr, .feed-entry, .activity"):
        a_link = row.find("a", href=re.compile(r"/activities/(\d+)"))
        if not a_link:
            continue
        m_aid = re.search(r"/activities/(\d+)", a_link.get("href", ""))
        if not m_aid:
            continue
        activity_id = m_aid.group(1)
        title = a_link.get_text(" ", strip=True) or None
        polyline = None
        for attr in ("data-polyline", "data-encoded-polyline", "data-summary-polyline"):
            v = row.get(attr) if hasattr(row, "get") else None
            if v:
                polyline = v
                break
        if polyline is None:
            inner = row.find(attrs={"data-polyline": True}) or row.find(
                attrs={"data-encoded-polyline": True}
            )
            if inner is not None:
                polyline = (
                    inner.get("data-polyline")
                    or inner.get("data-encoded-polyline")
                )
        out["recent_activities"].append(
            {
                "activity_id": activity_id,
                "title": title,
                "polyline": polyline,
            }
        )

    return out


def _parse_routes(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    routes: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=re.compile(r"/routes/(\d+)")):
        m = re.search(r"/routes/(\d+)", a["href"])
        if not m:
            continue
        rid = m.group(1)
        if any(r["route_id"] == rid for r in routes):
            continue
        # Walk up to the row/card to harvest distance + type.
        container = a.find_parent(["tr", "li", "div"]) or a
        text = container.get_text(" ", strip=True) if container else ""
        m_dist = re.search(r"(\d+(?:[.,]\d+)?)\s*km", text, re.IGNORECASE)
        distance_km = None
        if m_dist:
            try:
                distance_km = float(m_dist.group(1).replace(",", "."))
            except ValueError:
                distance_km = None
        m_type = re.search(r"\b(Ride|Run|Hike|Walk|Swim|Gravel|MTB)\b", text, re.IGNORECASE)
        rtype = m_type.group(1).lower() if m_type else None
        name = a.get_text(" ", strip=True) or None
        routes.append(
            {
                "route_id": rid,
                "name": name,
                "distance_km": distance_km,
                "type": rtype,
            }
        )
    return routes


async def _safe_get(c: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await c.get(url)
    except (httpx.HTTPError, OSError):
        return None


async def _resolve_via_searxng(c: httpx.AsyncClient, full_name: str) -> str | None:
    """Backwards-compatible single-id resolver (first candidate wins)."""
    cands = await _candidates_via_searxng(c, full_name)
    return cands[0] if cands else None


async def _candidates_via_searxng(
    c: httpx.AsyncClient, full_name: str
) -> list[str]:
    """Return de-duplicated list of athlete_ids extracted from the dork."""
    out: list[str] = []
    try:
        s = get_settings()
        r = await c.get(
            f"{s.searxng_url}/search",
            params={
                "q": f'site:strava.com/athletes "{full_name}"',
                "format": "json",
            },
        )
    except (httpx.HTTPError, OSError):
        return out
    if r.status_code != 200:
        return out
    try:
        data = r.json()
    except ValueError:
        return out
    for it in data.get("results", []) or []:
        aid = _athlete_id_from_url(it.get("url"))
        if aid and aid not in out:
            out.append(aid)
    return out


async def _rank_candidates_by_city(
    c: httpx.AsyncClient, candidates: list[str], city: str
) -> tuple[str | None, dict[str, Any]]:
    """Fetch up to 5 candidate profiles, parse hometown, return the first that
    matches ``city`` (case-insensitive substring match).

    Returns a tuple ``(athlete_id_or_None, match_score_dict)``.
    """
    needle = (city or "").strip().lower()
    score: dict[str, Any] = {
        "city_query": city,
        "candidates_seen": 0,
        "candidates_inspected": [],
        "matched": False,
        "reason": None,
    }
    if not needle or not candidates:
        return None, score
    for aid in candidates[:5]:
        score["candidates_seen"] += 1
        resp = await _safe_get(c, PROFILE_URL.format(slug=aid))
        if resp is None or resp.status_code != 200:
            score["candidates_inspected"].append(
                {"athlete_id": aid, "hometown": None, "matched": False}
            )
            continue
        parsed = _parse_profile(resp.text or "")
        hometown = (parsed.get("hometown") or "")
        matched = needle in hometown.lower()
        score["candidates_inspected"].append(
            {
                "athlete_id": aid,
                "hometown": hometown or None,
                "matched": matched,
            }
        )
        if matched:
            score["matched"] = True
            score["reason"] = f"hometown '{hometown}' contains city '{city}'"
            return aid, score
    score["reason"] = "no candidate hometown matched city"
    return None, score


@register
class StravaPublicCollector(Collector):
    name = "strava_public"
    category = "sport"
    needs = ("username", "full_name", "extra_context")
    timeout_seconds = 30
    description = "Strava public profile + routes scraper (no auth)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        athlete_id: str | None = _athlete_id_from_extra(input.extra_context)
        username = (input.username or "").lstrip("@") or None
        confidence_match = 0.8
        confidence_candidate = 0.5
        confidence_strong = 0.95   # hometown matched the input city
        confidence_weak = 0.4      # multiple candidates, no hometown match
        confidence: float = confidence_match if athlete_id else confidence_candidate
        match_score: dict[str, Any] | None = None

        async with await get_client("gentle") as c:
            # Step 1 — resolve athlete_id if not supplied directly.
            profile_html: str | None = None
            profile_url: str | None = None

            if not athlete_id and username:
                url = PROFILE_URL.format(slug=username)
                resp = await _safe_get(c, url)
                if resp is not None and resp.status_code == 200:
                    profile_html = resp.text or ""
                    profile_url = str(resp.url)
                    athlete_id = _athlete_id_from_url(profile_url) or _athlete_id_from_html(
                        profile_html
                    )
                    if athlete_id:
                        confidence = confidence_match

            if not athlete_id and input.full_name:
                candidates = await _candidates_via_searxng(c, input.full_name)
                # WIRING: rank multiple candidates by city/hometown match so a
                # case with only (full_name, city) can pick the right athlete
                # without an explicit username/extra_context hint.
                if len(candidates) > 1 and (input.city or "").strip():
                    ranked, match_score = await _rank_candidates_by_city(
                        c, candidates, input.city or ""
                    )
                    if ranked:
                        athlete_id = ranked
                        confidence = confidence_strong
                    else:
                        # Legacy fallback: keep first candidate but lower confidence.
                        athlete_id = candidates[0]
                        confidence = confidence_weak
                elif candidates:
                    athlete_id = candidates[0]
                    confidence = confidence_match

            # Step 1.5 — OAuth global token fallback. SearXNG cannot index
            # /athletes pages because Strava blocks crawlers, so when no
            # athlete_id has been resolved yet, query any globally-linked
            # OAuth token's /api/v3/athlete endpoint and accept it iff the
            # returned identity matches the case input by name OR city.
            if not athlete_id:
                try:
                    from sqlalchemy import text as _sql_text

                    from app.db import session_scope as _scope
                    from app.integrations.strava_oauth import decrypt as _decrypt

                    async with _scope() as _s:
                        global_rows = (await _s.execute(
                            _sql_text(
                                "SELECT athlete_id, access_token_enc "
                                "FROM strava_tokens WHERE case_id IS NULL "
                                "ORDER BY created_at DESC LIMIT 5"
                            )
                        )).mappings().all()
                    for row in global_rows:
                        try:
                            tok = _decrypt(row["access_token_enc"])
                            r = await c.get(
                                "https://www.strava.com/api/v3/athlete",
                                headers={"Authorization": f"Bearer {tok}"},
                                timeout=10,
                            )
                            if r.status_code != 200:
                                continue
                            data = r.json()
                            cand_name = " ".join(
                                filter(None, [data.get("firstname"), data.get("lastname")])
                            ).strip().lower()
                            cand_city = (data.get("city") or "").strip().lower()
                            input_name = (input.full_name or "").strip().lower()
                            input_city = (input.city or "").strip().lower()
                            name_match = (
                                input_name and cand_name
                                and (input_name in cand_name or cand_name in input_name)
                            )
                            city_match = input_city and cand_city and input_city == cand_city
                            if name_match or city_match:
                                athlete_id = str(row["athlete_id"])
                                confidence = confidence_strong
                                match_score = {
                                    "method": "oauth_global_token",
                                    "name_match": bool(name_match),
                                    "city_match": bool(city_match),
                                    "candidate_name": cand_name,
                                    "candidate_city": cand_city,
                                }
                                profile_html = None  # force fetch of canonical /athletes/{id}
                                break
                        except Exception:  # noqa: BLE001
                            continue
                except Exception:  # noqa: BLE001
                    pass

            if not athlete_id:
                return

            # Step 2 — fetch the canonical profile page if we don't already have it.
            if profile_html is None:
                resp = await _safe_get(c, PROFILE_URL.format(slug=athlete_id))
                if resp is None or resp.status_code != 200:
                    return
                profile_html = resp.text or ""
                profile_url = str(resp.url)

            parsed = _parse_profile(profile_html)

            account_payload: dict[str, Any] = {
                "platform": "strava",
                "athlete_id": athlete_id,
                "username": username,
                "display_name": parsed["display_name"],
                "photo_url": parsed["photo_url"],
                "hometown": parsed["hometown"],
                "city": parsed["city"],
                "follower_count": parsed["follower_count"],
                "follow_count": parsed["follow_count"],
                "club_ids": parsed["club_ids"],
            }
            if match_score is not None:
                account_payload["match_score"] = match_score
            yield Finding(
                collector=self.name,
                category=self.category,
                entity_type="account",
                title=f"Strava: {parsed['display_name'] or username or athlete_id}",
                url=profile_url or PROFILE_URL.format(slug=athlete_id),
                confidence=confidence,
                payload=account_payload,
            )

            # Activity findings (with polyline if visible) — feeds A1.4 triangulation.
            for act in parsed["recent_activities"]:
                act_payload: dict[str, Any] = {
                    "platform": "strava",
                    "athlete_id": athlete_id,
                    "activity_id": act["activity_id"],
                    "title": act.get("title"),
                }
                if act.get("polyline"):
                    act_payload["polyline"] = act["polyline"]
                yield Finding(
                    collector=self.name,
                    category=self.category,
                    entity_type="activity",
                    title=f"Strava activity {act['activity_id']}",
                    url=f"https://www.strava.com/activities/{act['activity_id']}",
                    confidence=confidence,
                    payload=act_payload,
                )

            # Step 3 — public saved routes.
            resp = await _safe_get(c, ROUTES_URL.format(athlete_id=athlete_id))
            if resp is None or resp.status_code != 200:
                return
            for route in _parse_routes(resp.text or ""):
                yield Finding(
                    collector=self.name,
                    category=self.category,
                    entity_type="route",
                    title=f"Strava route: {route.get('name') or route['route_id']}",
                    url=f"https://www.strava.com/routes/{route['route_id']}",
                    confidence=confidence,
                    payload={
                        "platform": "strava",
                        "athlete_id": athlete_id,
                        "route_id": route["route_id"],
                        "name": route.get("name"),
                        "distance_km": route.get("distance_km"),
                        "type": route.get("type"),
                    },
                )


__all__ = [
    "StravaPublicCollector",
    "PROFILE_URL",
    "ROUTES_URL",
    "get_client",
]
