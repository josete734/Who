# WIRING: NOT registered in app/collectors/__init__.py — add `from app.collectors import linkedin_public  # noqa: F401` there to enable.
"""LinkedIn public profile collector.

Strategy:
  1. If `username` (a /in/<slug>) is provided, fetch
     ``https://www.linkedin.com/in/{username}`` directly.
  2. Otherwise, ask the local SearXNG meta-search for ``site:linkedin.com/in/``
     dorks built from the full name and email, and pick the first plausible
     /in/ slug.
  3. Parse the public HTML for OpenGraph tags and any embedded
     application/ld+json blocks (LinkedIn ships a ProfilePage / Person graph
     for SEO that survives most of their public-profile HTML pruning).

The endpoint is famously hostile to scraping: 999 (challenge), 403 (region
block), 429 (rate-limit) and full bot-walls are normal. We honor robots
implicitly by hitting only the public profile URL with a realistic browser
User-Agent, never authenticated endpoints, and we back off cleanly on any
non-200 with an empty result. Output is deterministic for cassette replay.

Inputs:
  username | full_name | email
Findings (PublicProfile entity_type), payload:
  profile_url, headline, current_company, location, photo_url,
  education[], experience[]

# DEPS: beautifulsoup4
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


_BLOCK_STATUSES = {403, 429, 451, 503, 999}
_PROFILE_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-_%.]+)/?", re.IGNORECASE)


@register
class LinkedInPublicCollector(Collector):
    name = "linkedin_public"
    category = "social"
    needs = ("username", "full_name", "email")
    timeout_seconds = 30
    description = "LinkedIn public /in/ profile via OpenGraph + JSON-LD scrape."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        slug = _slug_from_input(input)
        async with client(
            timeout=15,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
            },
        ) as c:
            if not slug:
                slug = await _resolve_slug_via_searxng(c, input)
            if not slug:
                return

            url = f"https://www.linkedin.com/in/{slug}"
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                return

            if r.status_code in _BLOCK_STATUSES or r.status_code >= 400:
                return
            if r.status_code != 200:
                return

            html = r.text or ""
            if not html or "authwall" in html[:4000].lower() and "og:title" not in html[:4000].lower():
                # Pure auth-wall response with no OG metadata — nothing to mine.
                return

            data = _parse_profile_html(html, url)
            if not data:
                return

            title = data.get("og_title") or f"LinkedIn: {slug}"
            yield Finding(
                collector=self.name,
                category="username",
                entity_type="LinkedInPublicProfile",
                title=f"LinkedIn: {title[:160]}",
                url=data.get("profile_url") or url,
                confidence=0.85 if input.username else 0.6,
                payload={
                    "profile_url": data.get("profile_url") or url,
                    "headline": data.get("headline"),
                    "current_company": data.get("current_company"),
                    "location": data.get("location"),
                    "photo_url": data.get("photo_url"),
                    "education": data.get("education", []),
                    "experience": data.get("experience", []),
                    "og_title": data.get("og_title"),
                    "og_description": data.get("og_description"),
                    "source": "linkedin_public_html",
                },
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug_from_input(i: SearchInput) -> str | None:
    """Extract a /in/<slug> from explicit fields when present."""
    if i.linkedin_url:
        m = _PROFILE_RE.search(i.linkedin_url)
        if m:
            return m.group(1).strip("/")
    if i.username:
        # accept either bare slug or full URL passed as username
        m = _PROFILE_RE.search(i.username)
        if m:
            return m.group(1).strip("/")
        u = i.username.lstrip("@").strip("/")
        # Bare usernames are commonly the same as the linkedin slug.
        if re.fullmatch(r"[A-Za-z0-9\-_.]{3,100}", u):
            return u
    return None


async def _resolve_slug_via_searxng(c: httpx.AsyncClient, i: SearchInput) -> str | None:
    """Fall back to SearXNG dorks like `site:linkedin.com/in/ "Full Name"`.

    Deterministic: takes the first matching /in/<slug> across query attempts.
    """
    s = get_settings()
    queries: list[str] = []
    for nm in i.name_variants():
        ctx = f' "{i.city}"' if i.city else ""
        queries.append(f'site:linkedin.com/in/ "{nm}"{ctx}')
    if i.email:
        queries.append(f'"{i.email}" site:linkedin.com/in/')
    if not queries:
        return None

    for q in queries:
        try:
            r = await c.get(
                f"{s.searxng_url}/search",
                params={"q": q, "format": "json", "language": s.default_language},
            )
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except ValueError:
            continue
        for it in data.get("results", [])[:10]:
            url = it.get("url") or ""
            m = _PROFILE_RE.search(url)
            if m:
                slug = m.group(1).strip("/")
                # filter obvious noise like /in/jobs or /in/directory
                if slug.lower() in {"jobs", "directory", "login", "signup"}:
                    continue
                return slug
    return None


def _parse_profile_html(html: str, url: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")

    out: dict[str, Any] = {"profile_url": url}

    # OpenGraph
    def _og(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return None

    og_title = _og("og:title")
    og_desc = _og("og:description")
    og_img = _og("og:image")
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href"):
        href = canonical["href"]
        if "linkedin.com/in/" in href:
            out["profile_url"] = href

    out["og_title"] = og_title
    out["og_description"] = og_desc
    out["photo_url"] = og_img

    # JSON-LD (LinkedIn ships a ProfilePage with an embedded Person)
    educations: list[dict[str, Any]] = []
    experiences: list[dict[str, Any]] = []
    headline: str | None = None
    location: str | None = None
    current_company: str | None = None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        for node in _iter_graph(payload):
            t = node.get("@type")
            if t == "Person" or (isinstance(t, list) and "Person" in t):
                headline = headline or node.get("jobTitle") or node.get("description")
                addr = node.get("address") or {}
                if isinstance(addr, dict):
                    loc_parts = [
                        addr.get("addressLocality"),
                        addr.get("addressRegion"),
                        addr.get("addressCountry"),
                    ]
                    loc_str = ", ".join([p for p in loc_parts if p])
                    if loc_str:
                        location = location or loc_str
                if not out.get("photo_url"):
                    img = node.get("image")
                    if isinstance(img, dict):
                        out["photo_url"] = img.get("contentUrl") or img.get("url")
                    elif isinstance(img, str):
                        out["photo_url"] = img

                works_for = node.get("worksFor") or []
                if isinstance(works_for, dict):
                    works_for = [works_for]
                for w in works_for:
                    if not isinstance(w, dict):
                        continue
                    exp = {
                        "company": w.get("name"),
                        "url": w.get("url"),
                        "role": w.get("description") or w.get("jobTitle"),
                        "start": (w.get("member") or {}).get("startDate") if isinstance(w.get("member"), dict) else w.get("startDate"),
                        "end": (w.get("member") or {}).get("endDate") if isinstance(w.get("member"), dict) else w.get("endDate"),
                    }
                    experiences.append({k: v for k, v in exp.items() if v})
                    if not current_company:
                        current_company = w.get("name")

                alumni = node.get("alumniOf") or []
                if isinstance(alumni, dict):
                    alumni = [alumni]
                for a in alumni:
                    if not isinstance(a, dict):
                        continue
                    edu = {
                        "school": a.get("name"),
                        "url": a.get("url"),
                        "start": (a.get("member") or {}).get("startDate") if isinstance(a.get("member"), dict) else a.get("startDate"),
                        "end": (a.get("member") or {}).get("endDate") if isinstance(a.get("member"), dict) else a.get("endDate"),
                    }
                    educations.append({k: v for k, v in edu.items() if v})

    # Heuristic fallback from og:description, e.g.
    # "Cargo at Empresa · Educación: Universidad · Ubicación: Madrid · 500+ contactos…"
    if og_desc and (not headline or not current_company or not location):
        parts = [p.strip() for p in og_desc.split("·")]
        if parts:
            head = parts[0]
            if " at " in head and not current_company:
                hl, _, comp = head.partition(" at ")
                headline = headline or hl.strip()
                current_company = current_company or comp.strip()
            elif " en " in head and not current_company:
                hl, _, comp = head.partition(" en ")
                headline = headline or hl.strip()
                current_company = current_company or comp.strip()
            else:
                headline = headline or head
        for p in parts[1:]:
            low = p.lower()
            if not location and (low.startswith("ubicación") or low.startswith("location")):
                _, _, val = p.partition(":")
                location = val.strip() or None

    out["headline"] = headline
    out["current_company"] = current_company
    out["location"] = location
    # Deterministic ordering for cache friendliness.
    out["education"] = sorted(educations, key=lambda d: (d.get("school") or "", d.get("start") or ""))
    out["experience"] = sorted(experiences, key=lambda d: (d.get("company") or "", d.get("start") or ""))

    # If we didn't get *anything* useful, signal no-finding.
    if not (out.get("og_title") or headline or current_company or experiences or educations):
        return None
    return out


def _iter_graph(payload: Any) -> list[dict[str, Any]]:
    """Yield dict nodes from a JSON-LD payload (handles @graph and lists)."""
    nodes: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for p in payload:
            nodes.extend(_iter_graph(p))
    elif isinstance(payload, dict):
        if "@graph" in payload and isinstance(payload["@graph"], list):
            for n in payload["@graph"]:
                if isinstance(n, dict):
                    nodes.append(n)
        else:
            nodes.append(payload)
    return nodes


__all__ = ["LinkedInPublicCollector"]
