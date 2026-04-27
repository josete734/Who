"""Pastebin / IDE-paste search collector.

Searches public paste sites via the local SearXNG instance (site-restricted
dorks), then fetches each candidate paste's raw body (capped at 256KB) and
flags occurrences of the input value when found near sensitive context such
as ``password``, ``api_key``, ``token``, ``secret``.

Free, no API keys. Inputs: email, username, full_name, domain.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register  # noqa: F401  # `register` kept for symmetry
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput

# WIRING: This collector is intentionally NOT registered in the registry.
# To enable, decorate ``PastebinSearchCollector`` with ``@register`` and add
# it to the orchestrator's collector set. See ``app/collectors/base.py``.

PASTE_SITES: tuple[str, ...] = (
    "pastebin.com",
    "ghostbin.com",
    "rentry.co",
    "hastebin.com",
    "0bin.net",
    "justpaste.it",
)

MAX_PASTE_BYTES = 256 * 1024  # 256KB cap on paste body
REQUEST_TIMEOUT = 12.0
MAX_RESULTS_PER_QUERY = 8

# Sensitive-context regex — case-insensitive search on a window around the input hit.
SENSITIVE_RE = re.compile(
    r"(password|passwd|pwd|api[_\-\s]?key|access[_\-\s]?key|secret|token|bearer|authorization|aws[_\-]?secret|client[_\-]?secret)",
    re.IGNORECASE,
)

# Window of characters around the input match to scan for sensitive context.
CONTEXT_WINDOW = 160


def _query_terms(i: SearchInput) -> list[tuple[str, str]]:
    """(label, term) pairs to search verbatim across paste sites."""
    out: list[tuple[str, str]] = []
    if i.email:
        out.append(("email", i.email))
    if i.username:
        out.append(("username", i.username))
    if i.full_name:
        out.append(("full_name", i.full_name))
    if i.domain:
        out.append(("domain", i.domain))
    return out


def _redact(snippet: str, term: str) -> str:
    """Redact the literal input term from a snippet so we never store PII verbatim."""
    if not term:
        return snippet
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    return pattern.sub("[REDACTED]", snippet)


def _find_match(body: str, term: str) -> tuple[str, str] | None:
    """Return (match_kind, redacted_snippet) if the body contains ``term``.

    match_kind = "sensitive" when a sensitive keyword sits within the context
    window of the term occurrence, else "plain".
    """
    if not term or not body:
        return None
    idx = body.lower().find(term.lower())
    if idx < 0:
        return None
    start = max(0, idx - CONTEXT_WINDOW)
    end = min(len(body), idx + len(term) + CONTEXT_WINDOW)
    window = body[start:end]
    kind = "sensitive" if SENSITIVE_RE.search(window) else "plain"
    snippet = _redact(window.strip().replace("\n", " "), term)
    return kind, snippet[:600]


@register
class PastebinSearchCollector(Collector):
    name = "pastebin_search"
    category = "leak"
    needs = ("email", "username", "full_name", "domain")
    timeout_seconds = 180
    description = "Searches public paste sites (Pastebin, Ghostbin, Rentry, Hastebin, 0bin, JustPaste) via SearXNG and inspects raw bodies for sensitive context near the subject."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        terms = _query_terms(input)
        if not terms:
            return

        s = get_settings()
        searx_base = getattr(s, "searxng_url", None) or "http://searxng:8080"

        async with client(timeout=REQUEST_TIMEOUT) as c:
            for label, term in terms:
                for site in PASTE_SITES:
                    q = f'site:{site} "{term}"'
                    try:
                        r = await c.get(
                            f"{searx_base}/search",
                            params={"q": q, "format": "json"},
                            timeout=REQUEST_TIMEOUT,
                        )
                    except httpx.HTTPError:
                        continue
                    if r.status_code != 200:
                        continue
                    try:
                        data = r.json()
                    except ValueError:
                        continue

                    for it in (data.get("results") or [])[:MAX_RESULTS_PER_QUERY]:
                        url = it.get("url")
                        if not url:
                            continue
                        # Fetch raw paste body (size-capped).
                        body = ""
                        try:
                            resp = await c.get(url, timeout=REQUEST_TIMEOUT)
                            if resp.status_code == 200:
                                raw = resp.content[:MAX_PASTE_BYTES]
                                try:
                                    body = raw.decode(resp.encoding or "utf-8", errors="replace")
                                except (LookupError, TypeError):
                                    body = raw.decode("utf-8", errors="replace")
                        except httpx.HTTPError:
                            body = ""

                        match = _find_match(body, term) if body else None
                        if match is None:
                            # Fall back to engine snippet so we still surface the hit, redacted.
                            engine_snip = (it.get("content") or "")[:600]
                            if term.lower() not in engine_snip.lower():
                                continue
                            match_kind = "snippet_only"
                            snippet = _redact(engine_snip, term)
                        else:
                            match_kind, snippet = match

                        yield Finding(
                            collector=self.name,
                            category="leak",
                            entity_type="Paste",
                            title=f"[paste:{site}] {label} match ({match_kind})",
                            url=url,
                            confidence=0.6 if match_kind == "sensitive" else 0.4,
                            payload={
                                "paste_url": url,
                                "site": site,
                                "input_kind": label,
                                "match_kind": match_kind,
                                "snippet": snippet,
                                "query": q,
                            },
                        )
