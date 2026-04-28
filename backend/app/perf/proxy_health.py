"""Proxy pool health-check (Wave 6).

Tracks which proxies in the ``PROXIES`` env are reachable. Periodically
probes each one against ``checkip.amazonaws.com`` (or a configurable URL)
and stores dead entries in a Redis SET that the netfetch client filters
on every ``_next_proxy()`` call.

Why a SET and not a list? Order doesn't matter — we only need O(1)
membership checks. The SET key has a TTL so a temporary outage clears
itself even if the watcher is down.

Test-friendliness:

* ``probe_proxy`` accepts an injectable ``client_factory`` so tests can
  drive it without real network.
* ``mark_dead`` / ``is_dead`` / ``alive_proxies`` are all small and
  independently unit-testable.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)


__all__ = [
    "REDIS_DEAD_SET_KEY",
    "DEAD_TTL_SECONDS",
    "DEFAULT_PROBE_URL",
    "alive_proxies",
    "is_dead",
    "mark_dead",
    "mark_alive",
    "probe_proxy",
    "run_health_check_once",
]


REDIS_DEAD_SET_KEY = "netfetch:proxy:dead"
# 30-min TTL: dead state self-heals if the watcher is down or the proxy
# starts working again before the next sweep.
DEAD_TTL_SECONDS = 30 * 60
DEFAULT_PROBE_URL = "https://checkip.amazonaws.com/"


def _proxies_from_env() -> list[str]:
    raw = os.getenv("PROXIES", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Redis helpers — every function takes an ``aioredis``-compatible client
# (or a fake) so tests can drive it with ``fakeredis``.
# ---------------------------------------------------------------------------
async def is_dead(redis: Any, proxy: str) -> bool:
    if not proxy:
        return False
    try:
        return bool(await redis.sismember(REDIS_DEAD_SET_KEY, proxy))
    except Exception:  # noqa: BLE001 — fail-open: treat as alive
        return False


async def mark_dead(redis: Any, proxy: str) -> None:
    try:
        await redis.sadd(REDIS_DEAD_SET_KEY, proxy)
        await redis.expire(REDIS_DEAD_SET_KEY, DEAD_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log.debug("proxy_health.mark_dead_failed proxy=%s err=%s", proxy, exc)


async def mark_alive(redis: Any, proxy: str) -> None:
    try:
        await redis.srem(REDIS_DEAD_SET_KEY, proxy)
    except Exception as exc:  # noqa: BLE001
        log.debug("proxy_health.mark_alive_failed proxy=%s err=%s", proxy, exc)


async def alive_proxies(redis: Any, proxies: list[str]) -> list[str]:
    """Return the subset of ``proxies`` that aren't currently marked dead."""
    if not proxies:
        return []
    out: list[str] = []
    for p in proxies:
        if not await is_dead(redis, p):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Probe — single proxy, returns True if alive
# ---------------------------------------------------------------------------
async def probe_proxy(
    proxy: str,
    *,
    probe_url: str = DEFAULT_PROBE_URL,
    timeout: float = 8.0,
    client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> bool:
    """HEAD/GET ``probe_url`` through ``proxy``. True = alive, False = dead.

    A 200/204 response with any body is enough; any HTTP error, timeout
    or transport error is treated as dead.
    """
    factory = client_factory or (
        lambda: httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=False)
    )
    try:
        async with factory() as c:
            r = await c.get(probe_url)
        return 200 <= r.status_code < 300
    except (httpx.HTTPError, OSError) as exc:
        log.debug("proxy_health.probe_failed proxy=%s err=%s", proxy, exc)
        return False


# ---------------------------------------------------------------------------
# Sweep — probe every configured proxy, update the Redis SET
# ---------------------------------------------------------------------------
async def run_health_check_once(
    redis: Any,
    proxies: list[str] | None = None,
    *,
    probe_url: str = DEFAULT_PROBE_URL,
    timeout: float = 8.0,
    concurrency: int = 8,
    client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> dict[str, bool]:
    """Probe each proxy in parallel and update the dead-set accordingly.

    Returns ``{proxy: alive_bool}`` for the set that was probed. The
    dispatcher / collectors don't need this return value — it's there for
    operators and tests to inspect.
    """
    if proxies is None:
        proxies = _proxies_from_env()
    if not proxies:
        return {}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(proxy: str) -> tuple[str, bool]:
        async with sem:
            ok = await probe_proxy(
                proxy,
                probe_url=probe_url,
                timeout=timeout,
                client_factory=client_factory,
            )
        if ok:
            await mark_alive(redis, proxy)
        else:
            await mark_dead(redis, proxy)
        return proxy, ok

    results = await asyncio.gather(*(_one(p) for p in proxies))
    return dict(results)


# ---------------------------------------------------------------------------
# Arq cron entrypoint — one task per scheduled run.
# ---------------------------------------------------------------------------
async def proxy_health_cron(ctx: dict[str, Any]) -> int:
    """Arq task: probe all configured proxies and return live count.

    Wire into ``WorkerSettings.cron_jobs`` (e.g. every 5 min) when running
    the worker so the dead-set stays fresh.
    """
    proxies = _proxies_from_env()
    if not proxies:
        return 0
    try:
        import redis.asyncio as aioredis  # type: ignore

        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        client = aioredis.from_url(redis_url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("proxy_health.cron.redis_unavailable err=%s", exc)
        return 0

    try:
        results = await run_health_check_once(client, proxies)
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    alive_count = sum(1 for ok in results.values() if ok)
    log.info(
        "proxy_health.cron alive=%d/%d", alive_count, len(results)
    )
    return alive_count
