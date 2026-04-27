"""Fan-out orchestrator.

For a given case, runs every applicable Collector concurrently with a
Semaphore, persists Findings + CollectorRun rows, and publishes events to
the Redis event bus for SSE consumers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select, update

from app.collectors import collector_registry  # noqa: F401 — also triggers import of every collector
from app.collectors.base import Collector, Finding
from app.config import get_settings
from app.db import Case, CollectorRun, Finding as FindingRow, session_scope
from app.event_bus import publish
from app.schemas import SearchInput

# Observability — fail-soft if observability module not yet loadable in container
try:
    from app.observability.metrics import record_finding, track_case
except Exception:  # noqa: BLE001
    def record_finding(*a, **k): pass  # type: ignore
    from contextlib import contextmanager as _cm
    @_cm
    def track_case():  # type: ignore
        yield

try:
    from app.perf import async_phase
except Exception:  # noqa: BLE001
    from contextlib import asynccontextmanager as _acm
    @_acm
    async def async_phase(_name: str):  # type: ignore
        yield


# ---------------------------------------------------------------------------
# Adaptive parallelism configuration
# ---------------------------------------------------------------------------
# External rate-limited APIs (sport platforms, public registries, academic
# databases) tolerate far less concurrency than fast/cheap username/email/dns
# lookups. We split the global semaphore into category-aware buckets.
SLOW_CATEGORIES: frozenset[str] = frozenset({"sport", "registry", "academic"})
FAST_CATEGORIES: frozenset[str] = frozenset({"username", "email", "domain"})
SLOW_MAX_CONCURRENCY: int = 8
FAST_MAX_CONCURRENCY: int = 30

# Per-(case, collector) timeout streak counter and backoff delay (seconds).
# A simple in-process map; for multi-worker setups this could move to Redis,
# but keeping it local avoids extra round-trips on the hot path.
_TIMEOUT_STREAKS: dict[tuple[str, str], int] = defaultdict(int)
_BACKOFF_DELAYS: dict[tuple[str, str], float] = defaultdict(float)
TIMEOUT_BACKOFF_THRESHOLD: int = 2
TIMEOUT_BACKOFF_BASE_SECONDS: float = 2.0
TIMEOUT_BACKOFF_MAX_SECONDS: float = 30.0


def _bucket_for(collector: Collector) -> str:
    cat = (collector.category or "").lower()
    if cat in SLOW_CATEGORIES:
        return "slow"
    if cat in FAST_CATEGORIES:
        return "fast"
    return "default"


def record_collector_run(_name: str, _status: str) -> None:  # noqa: D401
    """Best-effort Prom counter — no-op if observability not present."""
    try:
        from app.observability.metrics import _get_or_create  # type: ignore
        from prometheus_client import Counter
        c = _get_or_create(Counter, "collector_runs_total", "Collector run outcomes",
                           ("collector", "status"))
        c.labels(_name, _status).inc()
    except Exception:  # noqa: BLE001
        pass


def record_collector_duration(_name: str, _seconds: float) -> None:
    try:
        from app.observability.metrics import _get_or_create  # type: ignore
        from prometheus_client import Histogram
        h = _get_or_create(Histogram, "collector_duration_seconds", "Collector duration",
                           ("collector",),
                           buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300))
        h.labels(_name).observe(_seconds)
    except Exception:  # noqa: BLE001
        pass


async def _run_one(case_id: uuid.UUID, collector: Collector, input: SearchInput) -> None:
    # Set ContextVars that some collectors (e.g. strava_authed) read to learn
    # the active case_id without having to thread it through SearchInput.
    try:
        from app.collectors.strava_authed import current_case_id as _strava_cid
        _strava_cid.set(str(case_id))
    except Exception:  # noqa: BLE001
        pass

    # Apply any dynamic backoff scheduled from prior timeouts on this case.
    streak_key = (str(case_id), collector.name)
    pre_delay = _BACKOFF_DELAYS.get(streak_key, 0.0)
    if pre_delay > 0:
        try:
            await asyncio.sleep(pre_delay)
        except Exception:  # noqa: BLE001
            pass

    started = dt.datetime.now(dt.timezone.utc)
    t0 = time.perf_counter()
    seen: set[str] = set()
    count = 0
    run_id = uuid.uuid4()

    async with session_scope() as s:
        s.add(CollectorRun(
            id=run_id,
            case_id=case_id,
            collector=collector.name,
            status="running",
            started_at=started,
        ))

    await publish(case_id, {
        "type": "collector_start",
        "case_id": str(case_id),
        "data": {"collector": collector.name, "category": collector.category},
    })

    status = "ok"
    message: str | None = None

    try:
        async with asyncio.timeout(collector.timeout_seconds):
            async for f in collector.run(input):
                fp = f.fingerprint()
                if fp in seen:
                    continue
                seen.add(fp)
                count += 1
                record_finding(collector.name, f.entity_type or "unknown")
                payload = {
                    "id": str(uuid.uuid4()),
                    "collector": f.collector,
                    "category": f.category,
                    "entity_type": f.entity_type,
                    "title": f.title,
                    "url": f.url,
                    "confidence": f.confidence,
                    "payload": f.payload,
                    "fingerprint": fp,
                }
                async with session_scope() as s:
                    s.add(FindingRow(
                        id=uuid.UUID(payload["id"]),
                        case_id=case_id,
                        collector=f.collector,
                        category=f.category,
                        entity_type=f.entity_type,
                        title=f.title[:500],
                        url=f.url,
                        confidence=f.confidence,
                        payload=f.payload,
                        fingerprint=fp,
                    ))
                # WIRING: when strava_public yields an athlete_id, eagerly
                # bind any pre-existing global OAuth token to this case so
                # strava_authed (which still selects by case_id) can use it.
                if f.collector == "strava_public" and isinstance(f.payload, dict):
                    aid_raw = f.payload.get("athlete_id")
                    if aid_raw:
                        try:
                            aid_int = int(aid_raw)
                        except (ValueError, TypeError):
                            aid_int = None
                        if aid_int is not None:
                            try:
                                from app.integrations.strava_oauth import (
                                    ensure_case_token_link as _ensure_link,
                                )
                                await _ensure_link(case_id, aid_int)
                            except Exception:  # noqa: BLE001
                                pass
                await publish(case_id, {
                    "type": "finding",
                    "case_id": str(case_id),
                    "data": payload,
                })
    except asyncio.TimeoutError:
        status = "timeout"
        message = f"Timeout after {collector.timeout_seconds}s"
    except Exception as e:  # noqa: BLE001
        status = "error"
        message = str(e)[:2000]

    # Update timeout streak + backoff state for next scheduling of this
    # (case, collector) pair within the same case run.
    if status == "timeout":
        _TIMEOUT_STREAKS[streak_key] += 1
        if _TIMEOUT_STREAKS[streak_key] > TIMEOUT_BACKOFF_THRESHOLD:
            # Exponential backoff capped to TIMEOUT_BACKOFF_MAX_SECONDS.
            n = _TIMEOUT_STREAKS[streak_key] - TIMEOUT_BACKOFF_THRESHOLD
            _BACKOFF_DELAYS[streak_key] = min(
                TIMEOUT_BACKOFF_BASE_SECONDS * (2 ** (n - 1)),
                TIMEOUT_BACKOFF_MAX_SECONDS,
            )
    else:
        # Reset on any non-timeout outcome.
        _TIMEOUT_STREAKS.pop(streak_key, None)
        _BACKOFF_DELAYS.pop(streak_key, None)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    record_collector_run(collector.name, status if status == "ok" else status)
    record_collector_duration(collector.name, duration_ms / 1000.0)
    async with session_scope() as s:
        await s.execute(
            update(CollectorRun)
            .where(CollectorRun.id == run_id)
            .values(
                status=status,
                findings_count=count,
                duration_ms=duration_ms,
                message=message,
                finished_at=dt.datetime.now(dt.timezone.utc),
            )
        )

    await publish(case_id, {
        "type": "collector_end",
        "case_id": str(case_id),
        "data": {
            "collector": collector.name,
            "status": status,
            "count": count,
            "duration_ms": duration_ms,
            "message": message,
        },
    })


async def run_case(case_id: uuid.UUID, input: SearchInput, llm: str = "claude") -> None:
    """Main entry: fan-out all applicable collectors, then synthesize with LLM."""
    with track_case():
        await _run_case_inner(case_id, input, llm)


async def _run_case_inner(case_id: uuid.UUID, input: SearchInput, llm: str = "claude") -> None:
    s = get_settings()
    # Bucketed semaphores for adaptive parallelism. ``default_sem`` honours
    # the existing global setting; ``slow_sem`` / ``fast_sem`` apply to
    # collectors in known rate-limited or known-cheap categories.
    default_sem = asyncio.Semaphore(s.max_concurrent_collectors)
    slow_sem = asyncio.Semaphore(min(SLOW_MAX_CONCURRENCY, s.max_concurrent_collectors))
    fast_sem = asyncio.Semaphore(max(FAST_MAX_CONCURRENCY, s.max_concurrent_collectors))
    bucket_sems = {"slow": slow_sem, "fast": fast_sem, "default": default_sem}

    # Mark case as running
    async with session_scope() as sess:
        await sess.execute(update(Case).where(Case.id == case_id).values(status="running"))

    collectors = collector_registry.applicable_for(input)
    await publish(case_id, {
        "type": "case_started",
        "case_id": str(case_id),
        "data": {"collectors": [c.name for c in collectors]},
    })

    async def guarded(c: Collector) -> None:
        sem = bucket_sems[_bucket_for(c)]
        async with sem:
            await _run_one(case_id, c, input)

    try:
        async with async_phase("collection"):
            await asyncio.gather(*(guarded(c) for c in collectors))
    except Exception as e:
        async with session_scope() as sess:
            await sess.execute(
                update(Case)
                .where(Case.id == case_id)
                .values(status="error", error=str(e)[:2000], finished_at=dt.datetime.now(dt.timezone.utc))
            )
        await publish(case_id, {"type": "error", "case_id": str(case_id), "data": {"error": str(e)[:1000]}})
        return

    # Spatial triangulation — fail-soft. If the case has accumulated enough
    # findings carrying GPS polylines (e.g. Strava activities), enqueue a
    # background post-processing job that clusters them into likely
    # home/work/gym dwell points.
    async with async_phase("triangulation"):
        try:
            from sqlalchemy import text as _sql_text

            async with session_scope() as _sess:
                poly_count = (await _sess.execute(
                    _sql_text(
                        "SELECT COUNT(*) FROM findings "
                        "WHERE case_id = :cid "
                        "AND payload ? 'polyline' "
                        "AND payload->>'polyline' IS NOT NULL "
                        "AND payload->>'polyline' <> ''"
                    ),
                    {"cid": str(case_id)},
                )).scalar_one()
            if poly_count and int(poly_count) >= 5:
                from arq import create_pool as _create_pool
                from app.tasks import WorkerSettings as _WS

                pool = await _create_pool(_WS.redis_settings)
                try:
                    await pool.enqueue_job("run_triangulation", str(case_id))
                finally:
                    try:
                        await pool.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            await publish(case_id, {"type": "warning", "case_id": str(case_id),
                                     "data": {"stage": "triangulation_enqueue", "error": str(e)[:500]}})

    # Entity resolution — fail-soft
    async with async_phase("entity_resolution"):
        try:
            from app.entity_resolution.engine import resolve as _resolve_entities
            await _resolve_entities(case_id)
        except Exception as e:  # noqa: BLE001
            await publish(case_id, {"type": "warning", "case_id": str(case_id),
                                     "data": {"stage": "entity_resolution", "error": str(e)[:500]}})

    # Synthesis
    if llm in ("claude", "gemini"):
        async with async_phase("synthesis"):
            try:
                from app.llm.synthesis import synthesize
                await synthesize(case_id, llm)
            except Exception as e:  # noqa: BLE001
                await publish(case_id, {
                    "type": "error",
                    "case_id": str(case_id),
                    "data": {"stage": "synthesis", "error": str(e)[:1000]},
                })

    async with session_scope() as sess:
        await sess.execute(
            update(Case)
            .where(Case.id == case_id)
            .values(status="done", finished_at=dt.datetime.now(dt.timezone.utc))
        )

    await publish(case_id, {"type": "done", "case_id": str(case_id), "data": {}})
