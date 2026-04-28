"""Jina AI Reader gateway (Wave 7).

``r.jina.ai/{URL}`` is a free, no-key endpoint that fetches a web page,
strips ads/navigation, and returns clean Markdown. We use it as the input
to LLM-based parsers when CSS selectors break — markdown is far cheaper
to feed to Gemini Flash than raw HTML, and Jina's heuristics already
filter most of the noise.

Quota: Jina advertises ~1M tokens / month free per source IP. We cache
results in Redis for 24 h so a flaky pipeline doesn't burn through that
allowance.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


__all__ = ["JINA_BASE", "JINA_CACHE_TTL", "fetch_markdown", "cache_key"]


JINA_BASE = "https://r.jina.ai/"
JINA_CACHE_TTL = 24 * 3600  # 24 h


def cache_key(url: str) -> str:
    """Stable cache key for a given URL."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"jina:md:{digest}"


async def fetch_markdown(
    url: str,
    *,
    redis: Any | None = None,
    timeout: float = 25.0,
    client_factory: Any | None = None,
) -> str | None:
    """Fetch ``url`` through Jina Reader, returning the Markdown body.

    On cache hit, returns the cached value without network. On cache miss,
    issues a single GET to ``r.jina.ai/{url}`` and stores the response.

    Returns ``None`` on any error so callers can fall back to their
    pre-existing parsing pipeline. We never raise — failure must not
    break a collector run.

    Parameters
    ----------
    redis: optional aioredis-compatible client; if absent, behaves
        cache-less (every call hits the network).
    client_factory: optional callable returning an ``httpx.AsyncClient``-
        like context manager. Tests inject this to avoid real I/O.
    """
    if not url:
        return None

    key = cache_key(url)
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached:
                return cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        except Exception as exc:  # noqa: BLE001
            log.debug("jina.cache_get_failed key=%s err=%s", key, exc)

    factory = client_factory or (
        lambda: httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    )
    full_url = JINA_BASE + url
    try:
        async with factory() as c:
            r = await c.get(
                full_url,
                headers={
                    "Accept": "text/markdown",
                    "X-Return-Format": "markdown",
                },
            )
        if r.status_code != 200:
            log.debug("jina.bad_status url=%s status=%s", url, r.status_code)
            return None
        body = r.text or ""
    except (httpx.HTTPError, OSError) as exc:
        log.debug("jina.fetch_failed url=%s err=%s", url, exc)
        return None

    if redis is not None and body:
        try:
            await redis.setex(key, JINA_CACHE_TTL, body)
        except Exception as exc:  # noqa: BLE001
            log.debug("jina.cache_set_failed key=%s err=%s", key, exc)

    return body
