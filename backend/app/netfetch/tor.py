"""Tor SOCKS5 helpers for .onion routing."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from .headers import random_headers


def is_onion(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.endswith(".onion")


def tor_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """Build an AsyncClient routed through Tor SOCKS5 (env TOR_SOCKS)."""
    socks = os.getenv("TOR_SOCKS", "socks5://tor:9050")
    return httpx.AsyncClient(
        proxy=socks,
        timeout=timeout,
        headers=random_headers(),
        follow_redirects=True,
    )
