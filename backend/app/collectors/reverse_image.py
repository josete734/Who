"""Reverse image lookup collector (Wave 3 / C8 + Ola 3 / A3.2).

Given a photo URL (newly produced ``photo_url`` pivot kind from B3) or a local
SHA-256 of a downloaded image, query reverse-image search providers and emit
matches. Strategies tried, in order:

1. **TinEye Commercial API** — when ``TINEYE_API_KEY`` is configured.
2. **Google Lens via SerpAPI** — when ``SERPAPI_KEY`` is configured.
3. **Yandex HTML scrape** — no auth, brittle (``confidence=0.4``).
4. **Bing Visual** — no auth, scraped (``confidence=0.5``).
5. **TinEye web** — no auth, multipart POST to result_json (``confidence=0.5``).

The keyless backends (Yandex/Bing/TinEye-web) all use ``netfetch.get_client``;
on HTTP 429 they automatically retry through ``get_client('tor')``. They emit
per-match findings with ``entity_type='image_match'`` and a structured payload
``{engine, source_url, page_url, score, dimensions}``. Confidence 0.5 (scraped
results are noisy).

Findings carry ``matches[]`` with ``{url, page_url, dimensions,
similarity_score, source}``.

This collector is **intentionally not registered**: it's invoked from the
photo-pivot dispatcher rather than the generic per-case fan-out. See
``app.pivot.dispatcher`` for wiring.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.netfetch import get_client
from app.schemas import SearchInput

# WIRING: settings are read at runtime via ``app.dynamic_settings.get_runtime``.
# Keys consumed:
#   - TINEYE_API_KEY    — TinEye Commercial REST credentials (basic auth user)
#   - TINEYE_API_SECRET — optional; if present used as basic auth password
#   - SERPAPI_KEY       — SerpAPI token for Google Lens engine
# Add these to ``app.config.Settings`` (or the dynamic-settings store) before
# enabling this collector in production.
from app.dynamic_settings import get_runtime

TINEYE_ENDPOINT = "https://api.tineye.com/rest/search/"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
YANDEX_ENDPOINT = "https://yandex.com/images/search"
BING_VISUAL_ENDPOINT = "https://www.bing.com/images/search"
TINEYE_WEB_ENDPOINT = "https://tineye.com/result_json/"


@register
class ReverseImageCollector(Collector):
    name = "reverse_image"
    category = "image"
    # ``photo_url`` is not a top-level SearchInput field — the pivot dispatcher
    # passes it via ``extra_context`` (see app.pivot.policy). A local sha256 may
    # be supplied the same way (prefixed ``sha256:``).
    needs = ("extra_context",)
    timeout_seconds = 30
    description = "Reverse-image lookup (TinEye → Lens → Yandex/Bing/TinEye-web)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        photo_url, sha256 = _extract_photo(input)
        if not photo_url and not sha256:
            return

        rt = await get_runtime()
        tineye_key = (rt.get("TINEYE_API_KEY") or "").strip()
        tineye_secret = (rt.get("TINEYE_API_SECRET") or "").strip()
        serpapi_key = (rt.get("SERPAPI_KEY") or "").strip()

        matches: list[dict[str, Any]] = []
        source = ""
        confidence = 0.7

        if tineye_key and photo_url:
            try:
                matches = await _query_tineye(photo_url, tineye_key, tineye_secret)
                source = "tineye"
                confidence = 0.85
            except Exception:  # noqa: BLE001 — best-effort; fall through
                matches = []

        if not matches and serpapi_key and photo_url:
            try:
                matches = await _query_google_lens(photo_url, serpapi_key)
                source = "google_lens"
                confidence = 0.75
            except Exception:  # noqa: BLE001
                matches = []

        # ----- Keyless scraped backends (A3.2) ---------------------------
        # We aggregate matches from every backend that returns something, so
        # downstream consumers see a single bundle plus per-match findings.
        scraped: list[dict[str, Any]] = []
        if not matches and photo_url:
            for engine, fn in (
                ("yandex", _query_yandex),
                ("bing", _query_bing_visual),
                ("tineye_web", _query_tineye_web),
            ):
                try:
                    found = await fn(photo_url)
                except Exception:  # noqa: BLE001
                    found = []
                if found:
                    scraped.extend(found)
                    # Emit per-match finding for each scraped hit.
                    for m in found:
                        yield Finding(
                            collector=self.name,
                            category="image",
                            entity_type="image_match",
                            title=f"{engine}: {m.get('page_url') or m.get('url') or '?'}",
                            url=m.get("page_url") or m.get("url") or photo_url,
                            confidence=0.5,
                            payload={
                                "engine": engine,
                                "source_url": photo_url,
                                "page_url": m.get("page_url"),
                                "score": m.get("similarity_score"),
                                "dimensions": m.get("dimensions"),
                            },
                        )

        if not matches and scraped:
            matches = scraped
            source = "scraped"
            confidence = 0.5

        if not matches:
            return

        yield Finding(
            collector=self.name,
            category="image",
            entity_type="ReverseImageMatchSet",
            title=f"Reverse image matches via {source} ({len(matches)})",
            url=photo_url,
            confidence=confidence,
            payload={
                "source": source,
                "query_url": photo_url,
                "query_sha256": sha256,
                "matches": matches,
            },
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_photo(input: SearchInput) -> tuple[str | None, str | None]:
    """Pull ``photo_url`` / ``sha256:`` token out of extra_context."""
    ctx = (input.extra_context or "").strip()
    if not ctx:
        return None, None
    photo_url = None
    sha256 = None
    for tok in re.split(r"\s+", ctx):
        if tok.startswith(("http://", "https://")) and not photo_url:
            photo_url = tok
        elif tok.startswith("sha256:") and not sha256:
            sha256 = tok.split(":", 1)[1]
    return photo_url, sha256


async def _fetch_with_tor_fallback(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: Any = None,
    files: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Fetch via ``get_client('default')``; on 429, retry through Tor.

    All keyless backends share this helper so the Tor escalation is uniform
    and the constraint "``get_client`` siempre" is honoured.
    """
    async def _do(policy: str) -> httpx.Response:
        c = await get_client(policy)  # type: ignore[arg-type]
        try:
            req = c.build_request(method, url, params=params, data=data, files=files, headers=headers)
            return await c.send(req)
        finally:
            await c.aclose()

    resp = await _do("default")
    if resp.status_code == 429:
        resp = await _do("tor")
    return resp


async def _query_tineye(image_url: str, key: str, secret: str) -> list[dict[str, Any]]:
    auth = (key, secret) if secret else None
    async with client(timeout=20) as c:
        r = await c.post(
            TINEYE_ENDPOINT,
            data={"image_url": image_url},
            auth=auth,
        )
    if r.status_code != 200:
        raise RuntimeError(f"TinEye HTTP {r.status_code}")
    data = r.json() or {}
    out: list[dict[str, Any]] = []
    for m in (data.get("results", {}) or {}).get("matches", [])[:25]:
        backlinks = m.get("backlinks") or [{}]
        first = backlinks[0] if backlinks else {}
        out.append({
            "url": first.get("url") or m.get("image_url"),
            "page_url": first.get("backlink"),
            "dimensions": [m.get("width"), m.get("height")],
            "similarity_score": m.get("score"),
            "source": "tineye",
        })
    return out


async def _query_google_lens(image_url: str, key: str) -> list[dict[str, Any]]:
    params = {"engine": "google_lens", "url": image_url, "api_key": key}
    async with client(timeout=20) as c:
        r = await c.get(SERPAPI_ENDPOINT, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"SerpAPI HTTP {r.status_code}")
    data = r.json() or {}
    out: list[dict[str, Any]] = []
    for m in (data.get("visual_matches") or [])[:25]:
        out.append({
            "url": m.get("image"),
            "page_url": m.get("link"),
            "dimensions": [m.get("image_width"), m.get("image_height")],
            "similarity_score": m.get("score"),
            "source": "google_lens",
        })
    return out


async def _query_yandex(image_url: str) -> list[dict[str, Any]]:
    """GET yandex search-by-image; parse ``data-bem`` JSON for similar pages."""
    params = {"rpt": "imageview", "url": image_url}
    r = await _fetch_with_tor_fallback("GET", YANDEX_ENDPOINT, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"Yandex HTTP {r.status_code}")
    html = r.text
    out: list[dict[str, Any]] = []

    # Strategy 1: parse data-bem JSON blobs that contain similar_pages /
    # cbir-similar payloads; Yandex stores results as escaped JSON.
    for blob in re.findall(r'data-bem=\'({[^\']+})\'', html):
        try:
            payload = json.loads(blob)
        except Exception:  # noqa: BLE001
            continue
        # Walk dicts for known keys.
        for key in ("serp-list", "cbir-similar", "similar-page"):
            container = payload.get(key) if isinstance(payload, dict) else None
            if not isinstance(container, dict):
                continue
            for item in container.get("items", []) or []:
                page = item.get("url") or item.get("link") or item.get("originalUrl")
                if page:
                    out.append({
                        "url": item.get("img_href") or item.get("thumb"),
                        "page_url": page,
                        "dimensions": [item.get("w"), item.get("h")],
                        "similarity_score": item.get("relevance"),
                        "source": "yandex",
                    })

    # Strategy 2: legacy regex for OtherSites class.
    if not out:
        for href in re.findall(r'href="(https?://[^"]+)"[^>]*class="[^"]*OtherSites', html)[:15]:
            out.append({
                "url": None,
                "page_url": href,
                "dimensions": None,
                "similarity_score": None,
                "source": "yandex",
            })

    # Strategy 3: generic "origin":{"url":...} JSON fragment.
    if not out:
        for href in re.findall(r'"origin":\{"url":"(https?:[^"]+?)"', html)[:15]:
            page = re.sub(r'\\u002F', '/', href)
            out.append({
                "url": page,
                "page_url": page,
                "dimensions": None,
                "similarity_score": None,
                "source": "yandex",
            })
    return out[:25]


async def _query_bing_visual(image_url: str) -> list[dict[str, Any]]:
    """Bing Visual Search: HEAD to acquire session cookie, then GET results.

    Bing requires a live session cookie before the imgurl query returns
    visually-similar markup; a cheap HEAD on the homepage primes it.
    """
    # Prime cookie jar via HEAD on the search root.
    await _fetch_with_tor_fallback("HEAD", "https://www.bing.com/")

    params = {
        "q": f"imgurl:{image_url}",
        "view": "detailv2",
        "iss": "sbiupload",
        "FORM": "IRSBIQ",
    }
    r = await _fetch_with_tor_fallback("GET", BING_VISUAL_ENDPOINT, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"Bing HTTP {r.status_code}")
    html = r.text
    out: list[dict[str, Any]] = []

    # Bing embeds visually-similar matches inside ``m="{...}"`` JSON blobs on
    # each image card; the ``murl`` and ``purl`` keys are stable.
    for blob in re.findall(r'm="(\{[^"]+\})"', html)[:25]:
        try:
            payload = json.loads(blob.replace("&quot;", '"'))
        except Exception:  # noqa: BLE001
            continue
        page = payload.get("purl") or payload.get("turl")
        if not page:
            continue
        out.append({
            "url": payload.get("murl"),
            "page_url": page,
            "dimensions": None,
            "similarity_score": None,
            "source": "bing",
        })
    return out


async def _query_tineye_web(image_url: str) -> list[dict[str, Any]]:
    """TinEye web search: multipart POST to result_json with optional CSRF."""
    # Fetch CSRF token if their HTML form requires one.
    csrf: str | None = None
    try:
        r0 = await _fetch_with_tor_fallback("GET", "https://tineye.com/")
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r0.text or "")
        if m:
            csrf = m.group(1)
    except Exception:  # noqa: BLE001
        csrf = None

    headers = {"Referer": "https://tineye.com/"}
    if csrf:
        headers["X-CSRFToken"] = csrf

    params = {"url": image_url}
    files = {"url": (None, image_url)}  # multipart form

    r = await _fetch_with_tor_fallback(
        "POST", TINEYE_WEB_ENDPOINT, params=params, files=files, headers=headers,
    )
    if r.status_code != 200:
        raise RuntimeError(f"TinEye-web HTTP {r.status_code}")
    try:
        data = r.json() or {}
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for m in (data.get("matches") or [])[:25]:
        backlinks = m.get("backlinks") or [{}]
        first = backlinks[0] if backlinks else {}
        out.append({
            "url": m.get("image_url") or first.get("url"),
            "page_url": first.get("backlink") or first.get("url"),
            "dimensions": [m.get("width"), m.get("height")],
            "similarity_score": m.get("score"),
            "source": "tineye_web",
        })
    return out


# Module-level escape hatch for callers that want to feed a URL directly
# (used by the pivot dispatcher which doesn't always have a SearchInput).
async def lookup(photo_url: str | None = None, sha256: str | None = None) -> list[dict[str, Any]]:
    parts = []
    if photo_url:
        parts.append(photo_url)
    if sha256:
        parts.append(f"sha256:{sha256}")
    si = SearchInput(extra_context=" ".join(parts) or None)
    findings = [f async for f in ReverseImageCollector().run(si)]
    # The aggregated bundle is always the last finding when present.
    bundles = [f for f in findings if f.entity_type == "ReverseImageMatchSet"]
    return bundles[-1].payload["matches"] if bundles else []


# Helper for tests / external callers building a Yandex URL.
def yandex_search_url(image_url: str) -> str:
    return f"{YANDEX_ENDPOINT}?rpt=imageview&url={quote_plus(image_url)}"
