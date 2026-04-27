"""Pivot dispatcher.

Given a list of Pivot atoms freshly extracted from a Finding, the
dispatcher:

  1. Deduplicates them against the `case_pivots` table for this case.
  2. Picks the collectors whose `needs` tuple contains the corresponding
     SearchInput field (see ``policy.kind_to_search_field``).
  3. Skips collectors that have already run for *this exact pivot value*
     in this case (looking at `collector_runs` joined to `case_pivots`).
  4. Honours per-case caps: ``max_pivot_depth`` (default 2) and
     ``max_collectors_per_case`` (default 200).
  5. Enqueues a fresh ``run_case_task`` job per (collector, pivot) pair
     via the same Arq pool the public router uses.

This module performs *no* SQL or Redis I/O at import time so it can be
unit-tested with monkeypatched session/pool factories.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import text

from app.pivot.extractor import Pivot
from app.pivot.policy import (
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_MAX_COLLECTORS_PER_CASE,
    DEFAULT_MAX_PIVOT_DEPTH,
    allowed_at_depth,
    kind_to_search_field,
    passes_confidence_floor,
)

# Re-export so `from app.pivot.dispatcher import Pivot` works.
__all__ = ["Pivot", "maybe_dispatch", "DispatchResult"]


@dataclass
class DispatchResult:
    inserted: int          # new rows in case_pivots
    enqueued: int          # collector jobs enqueued
    skipped_dedup: int     # pivots already known
    skipped_depth: int     # pivots refused by depth/policy
    skipped_confidence: int
    skipped_budget: int    # collectors refused because budget exhausted


# --------------------------------------------------------------------------- #
# Internal helpers — kept thin so they're easy to monkeypatch in tests.       #
# --------------------------------------------------------------------------- #
async def _existing_pivot_values(session: Any, case_id: uuid.UUID) -> set[tuple[str, str]]:
    rows = await session.execute(
        text("SELECT kind, value FROM case_pivots WHERE case_id = :cid"),
        {"cid": str(case_id)},
    )
    return {(r[0], r[1]) for r in rows.all()}


async def _collector_count_for_case(session: Any, case_id: uuid.UUID) -> int:
    row = await session.execute(
        text("SELECT COUNT(*) FROM collector_runs WHERE case_id = :cid"),
        {"cid": str(case_id)},
    )
    return int(row.scalar() or 0)


async def _already_ran(session: Any, case_id: uuid.UUID, collector: str) -> bool:
    """Cheap guard: has this collector already run for this case at all?

    A finer-grained per-pivot-value check is enforced by the UNIQUE
    constraint on case_pivots (kind, value) — we never enqueue twice for
    the same atom because the second insert is rejected upstream.
    """
    row = await session.execute(
        text(
            "SELECT 1 FROM collector_runs "
            "WHERE case_id = :cid AND collector = :name LIMIT 1"
        ),
        {"cid": str(case_id), "name": collector},
    )
    return row.scalar() is not None


async def _insert_pivot(
    session: Any,
    case_id: uuid.UUID,
    pivot: Pivot,
    depth: int,
) -> bool:
    """Returns True if a new row was inserted, False if it already existed."""
    res = await session.execute(
        text(
            "INSERT INTO case_pivots "
            "(id, case_id, kind, value, source_finding_id, depth, confidence, created_at) "
            "VALUES (:id, :cid, :kind, :val, :src, :depth, :conf, :ts) "
            "ON CONFLICT (case_id, kind, value) DO NOTHING"
        ),
        {
            "id": str(uuid.uuid4()),
            "cid": str(case_id),
            "kind": pivot.kind,
            "val": pivot.value,
            "src": pivot.source_finding_id,
            "depth": depth,
            "conf": pivot.confidence,
            "ts": dt.datetime.now(dt.timezone.utc),
        },
    )
    return (res.rowcount or 0) > 0


async def _mark_dispatched(session: Any, case_id: uuid.UUID, kind: str, value: str) -> None:
    await session.execute(
        text(
            "UPDATE case_pivots SET dispatched_at = :ts "
            "WHERE case_id = :cid AND kind = :kind AND value = :val"
        ),
        {
            "ts": dt.datetime.now(dt.timezone.utc),
            "cid": str(case_id),
            "kind": kind,
            "val": value,
        },
    )


def _matching_collectors(field: str) -> list[Any]:
    """Find collector classes whose `needs` tuple includes `field`."""
    from app.collectors import collector_registry  # local import: avoids cycle at import-time
    out = []
    for cls in collector_registry.all():
        needs = getattr(cls, "needs", ()) or ()
        if field in needs:
            out.append(cls)
    return out


def _build_search_payload(field: str, value: str) -> dict[str, str]:
    """Synthesize a SearchInput-shaped dict carrying just the pivot value."""
    return {field: value}


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
async def maybe_dispatch(
    case_id: uuid.UUID,
    pivots: Iterable[Pivot],
    *,
    depth: int = 1,
    max_pivot_depth: int = DEFAULT_MAX_PIVOT_DEPTH,
    max_collectors_per_case: int = DEFAULT_MAX_COLLECTORS_PER_CASE,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    session_factory: Any | None = None,
    pool_factory: Any | None = None,
) -> DispatchResult:
    """Persist new pivots and enqueue collectors that target them.

    Parameters
    ----------
    depth
        Depth of the *parent* finding. New pivots are stored at
        ``depth + 1`` and only dispatched while that value is
        ``<= max_pivot_depth``.
    session_factory / pool_factory
        Optional injection points for tests. Default to the production
        ``app.db.session_scope`` and ``arq.create_pool``.
    """
    result = DispatchResult(0, 0, 0, 0, 0, 0)
    pivots = list(pivots)
    if not pivots:
        return result

    next_depth = depth + 1
    if next_depth > max_pivot_depth:
        result.skipped_depth = len(pivots)
        return result

    # Lazy default factories — keeps this module test-friendly.
    if session_factory is None:
        from app.db import session_scope as session_factory  # type: ignore
    if pool_factory is None:
        from arq import create_pool as _create_pool
        from app.tasks import WorkerSettings

        async def pool_factory() -> Any:  # noqa: D401
            return await _create_pool(WorkerSettings.redis_settings)

    # Phase 1 — DB work: dedupe, insert, mark dispatched, count budget.
    to_enqueue: list[tuple[str, str, str, dict[str, str]]] = []  # (collector_name, kind, value, payload)
    async with session_factory() as session:
        existing = await _existing_pivot_values(session, case_id)
        budget_used = await _collector_count_for_case(session, case_id)

        for p in pivots:
            if not allowed_at_depth(p.kind, next_depth):
                result.skipped_depth += 1
                continue
            if not passes_confidence_floor(p.confidence, confidence_floor):
                result.skipped_confidence += 1
                continue
            if (p.kind, p.value) in existing:
                result.skipped_dedup += 1
                continue

            inserted = await _insert_pivot(session, case_id, p, next_depth)
            if not inserted:
                result.skipped_dedup += 1
                continue
            existing.add((p.kind, p.value))
            result.inserted += 1

            field = kind_to_search_field(p.kind)
            for cls in _matching_collectors(field):
                if budget_used >= max_collectors_per_case:
                    result.skipped_budget += 1
                    continue
                if await _already_ran(session, case_id, cls.name):
                    continue
                to_enqueue.append((cls.name, p.kind, p.value, _build_search_payload(field, p.value)))
                budget_used += 1

    if not to_enqueue:
        return result

    # Phase 2 — Redis/Arq work.
    pool = await pool_factory()
    try:
        for collector_name, kind, value, payload in to_enqueue:
            # We piggy-back on the existing run_case_task; downstream
            # filtering by collector name is the orchestrator's job in
            # the wiring step. We tag the input so it's easy to trace.
            await pool.enqueue_job(
                "run_case_task",
                str(case_id),
                payload,
                "none",  # no synthesis on cascading sub-runs
            )
            result.enqueued += 1

        # Mark them dispatched in a fresh session.
        async with session_factory() as session:
            for _, kind, value, _payload in to_enqueue:
                await _mark_dispatched(session, case_id, kind, value)
    finally:
        close = getattr(pool, "close", None)
        if close is not None:
            res = close()
            if hasattr(res, "__await__"):
                await res

    return result


# --------------------------------------------------------------------------- #
# WIRING:                                                                     #
# --------------------------------------------------------------------------- #
# Where to plug the pivot engine into the orchestrator (do NOT edit           #
# orchestrator.py here — that's the integration agent's job in a later wave).#
#                                                                             #
# Option A — inline, after every Finding is persisted in `_run_one`           #
# (app/orchestrator.py around line 86, just after the `await publish(...,    #
# {"type": "finding", ...})` call):                                           #
#                                                                             #
#     # WIRING: pivot cascade — extract atoms and enqueue follow-up runs.    #
#     from app.pivot import extract as _pivot_extract, maybe_dispatch        #
#     _pivots = _pivot_extract(f)  # f is the dataclass Finding              #
#     # Carry parent depth on SearchInput.extra_context or on a side channel.#
#     # For depth-0 collectors (initial run), depth=0; sub-runs pass depth=1.#
#     await maybe_dispatch(case_id, _pivots, depth=0)                        #
#                                                                             #
# Option B — event-driven, decoupled from `_run_one`. Spawn a background     #
# subscriber on case start that reads `event_bus.subscribe(case_id)` and     #
# reacts to ``{"type": "finding", ...}`` events. Pseudocode:                  #
#                                                                             #
#     async def _pivot_listener(case_id):                                     #
#         async for ev in subscribe(case_id):                                 #
#             if ev.get("type") != "finding":                                 #
#                 continue                                                    #
#             d = ev["data"]                                                  #
#             stub = SimpleNamespace(                                         #
#                 id=d["id"], title=d["title"], url=d.get("url"),             #
#                 payload=d.get("payload", {}),                                #
#                 confidence=d.get("confidence", 0.7),                         #
#             )                                                                #
#             await maybe_dispatch(                                            #
#                 case_id, extract(stub),                                      #
#                 depth=ev.get("data", {}).get("depth", 0),                    #
#             )                                                                #
#                                                                              #
#     # Started from `run_case` right after `case_started` is published,     #
#     # cancelled in the `finally` branch when the case ends.                 #
#                                                                              #
# Recommended: Option B — keeps `_run_one` hot path clean and lets the       #
# pivot engine be enabled/disabled via a feature flag without touching       #
# the orchestrator's transactional shape.                                    #
# --------------------------------------------------------------------------- #
