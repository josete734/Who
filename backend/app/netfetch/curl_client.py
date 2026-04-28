"""curl_cffi-backed HTTP client with browser TLS impersonation (Wave 6).

httpx ships with the Python ``ssl`` stdlib, which produces a JA3 fingerprint
that Cloudflare Bot Fight Mode, Akamai Bot Manager, PerimeterX and DataDome
all flag as "non-browser" before reading any HTTP header. ``curl_cffi``
wraps libcurl with TLS settings borrowed from real Chrome/Firefox builds,
giving us a TLS handshake that matches what those products actually expect
to see.

Usage:

    from app.netfetch.curl_client import curl_session, curl_get

    async with curl_session(impersonate="chrome124") as s:
        r = await s.get("https://www.linkedin.com/in/some-handle/")

The module is a soft dependency — if ``curl_cffi`` is not installed,
``CURL_AVAILABLE`` is False and ``curl_session`` raises a clear error.
``http_request_with_fallback`` falls back to httpx in that case.
"""
from __future__ import annotations

import logging
import random
from typing import Any

log = logging.getLogger(__name__)


try:
    from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found]

    CURL_AVAILABLE = True
except Exception:  # noqa: BLE001 — module may not be installed
    AsyncSession = None  # type: ignore[assignment]
    CURL_AVAILABLE = False


# Real-world browser tags supported by curl_cffi. See
# https://github.com/yifeikong/curl_cffi#sessions for the full list. We
# pick a small rotating pool to mimic browser-share variety. New entries
# can be added freely as long as curl_cffi recognises them.
_IMPERSONATE_POOL: tuple[str, ...] = (
    "chrome124",
    "chrome120",
    "chrome116",
    "edge101",
    "firefox120",
    "safari17_0",
)


def random_impersonate() -> str:
    """Pick a browser tag from the rotation pool."""
    return random.choice(_IMPERSONATE_POOL)  # noqa: S311 — not security-sensitive


class CurlNotAvailable(RuntimeError):
    """Raised when curl_cffi is not installed in the running environment."""


def curl_session(
    impersonate: str | None = None,
    *,
    timeout: float = 25.0,
    proxy: str | None = None,
    verify: bool = True,
) -> Any:
    """Build an ``AsyncSession`` with browser TLS impersonation.

    Returns the raw ``curl_cffi`` session — callers must use it as an
    async context manager (``async with curl_session(...) as s``). Raises
    ``CurlNotAvailable`` if ``curl_cffi`` is not installed; the caller is
    expected to fall back to httpx in that case.
    """
    if not CURL_AVAILABLE:
        raise CurlNotAvailable(
            "curl_cffi is not installed; install it or fall back to httpx"
        )
    tag = impersonate or random_impersonate()
    return AsyncSession(  # type: ignore[misc]
        impersonate=tag,
        timeout=timeout,
        proxy=proxy,
        verify=verify,
    )


async def curl_get(
    url: str,
    *,
    impersonate: str | None = None,
    timeout: float = 25.0,
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """One-shot GET via ``curl_cffi``. Returns the raw response object.

    The caller deals with the response shape (curl_cffi follows the
    requests-style API: ``.status_code``, ``.text``, ``.content``).
    """
    async with curl_session(
        impersonate=impersonate, timeout=timeout, proxy=proxy
    ) as s:
        return await s.get(url, headers=headers or {})


async def http_request_with_fallback(
    method: str,
    url: str,
    *,
    impersonate: str | None = None,
    timeout: float = 25.0,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
) -> Any:
    """Try ``curl_cffi`` first, fall back to ``httpx`` if it's missing.

    Designed for collectors that want browser-grade TLS but must keep
    working in environments without ``curl_cffi`` (e.g. minimal CI venvs).
    Returns a duck-typed response with ``.status_code`` and ``.text``;
    callers should treat the body as text or use ``r.content`` for bytes.
    """
    if CURL_AVAILABLE:
        async with curl_session(impersonate=impersonate, timeout=timeout) as s:
            method_l = method.lower()
            kwargs: dict[str, Any] = {"headers": headers or {}}
            if json_body is not None:
                kwargs["json"] = json_body
            fn = getattr(s, method_l)
            return await fn(url, **kwargs)

    # Fallback: plain httpx (no TLS impersonation, but still works).
    import httpx  # local import; avoid pulling httpx into module-load if not needed

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        return await c.request(method, url, headers=headers, json=json_body)


__all__ = [
    "CURL_AVAILABLE",
    "CurlNotAvailable",
    "curl_session",
    "curl_get",
    "http_request_with_fallback",
    "random_impersonate",
]
