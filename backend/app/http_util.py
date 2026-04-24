"""Shared HTTP utilities: httpx client factory, UA rotation, retry helper."""
from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

UA_POOL = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


def random_ua() -> str:
    return random.choice(UA_POOL)  # noqa: S311


def client(timeout: float = 15.0, **extra: Any) -> httpx.AsyncClient:
    headers = {
        "User-Agent": random_ua(),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    headers.update(extra.pop("headers", {}))
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        http2=True,
        **extra,
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout)),
)
async def get_json(url: str, **kwargs: Any) -> Any:
    async with client() as c:
        r = await c.get(url, **kwargs)
        r.raise_for_status()
        return r.json()


async def jitter_sleep(lo: float = 0.3, hi: float = 1.5) -> None:
    await asyncio.sleep(random.uniform(lo, hi))  # noqa: S311
