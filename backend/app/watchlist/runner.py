"""Arq cron task: re-runs each due watchlist case and fires alert.fired webhooks.

The runner loads enabled watchlists, kicks off ``run_case`` for each, then
diffs the resulting findings against ``last_results_hash``. On material
change it emits an ``alert.fired`` webhook event and persists the new hash.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.db import session_scope
from app.orchestrator import run_case
from app.schemas import SearchInput
from app.watchlist.model import diff_findings
from app.webhooks.dispatcher import dispatch

log = logging.getLogger(__name__)


async def _load_due() -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, owner, query_inputs, schedule_cron, "
                    "last_run_at, last_results_hash, enabled "
                    "FROM watchlist WHERE enabled = TRUE"
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _load_findings(case_id: uuid.UUID) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, collector, category, entity_type, title, url, "
                    "fingerprint FROM findings WHERE case_id = :cid"
                ),
                {"cid": case_id},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _persist_run(wl_id: uuid.UUID, new_hash: str) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE watchlist SET last_run_at = now(), "
                "last_results_hash = :h WHERE id = :id"
            ),
            {"id": wl_id, "h": new_hash},
        )
        await db.commit()


async def run_one(wl: dict[str, Any]) -> dict[str, Any]:
    """Re-run a single watchlist; emit webhooks on material change."""
    inputs = wl.get("query_inputs") or {}
    case_id = uuid.uuid4()
    try:
        await run_case(case_id, SearchInput(**inputs), llm="auto")
    except Exception as e:  # noqa: BLE001
        log.warning("watchlist.run.failed wl=%s err=%s", wl["id"], e)
        return {"id": str(wl["id"]), "ran": False, "error": str(e)[:200]}

    findings = await _load_findings(case_id)
    new_hash, changed = diff_findings(wl.get("last_results_hash"), findings)

    if changed:
        try:
            await dispatch(
                "alert.fired",
                {
                    "watchlist_id": str(wl["id"]),
                    "owner": wl.get("owner") or "",
                    "case_id": str(case_id),
                    "previous_hash": wl.get("last_results_hash"),
                    "new_hash": new_hash,
                    "findings_count": len(findings),
                    "fired_at": dt.datetime.utcnow().isoformat(),
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning("watchlist.dispatch.failed wl=%s err=%s", wl["id"], e)

    await _persist_run(wl["id"], new_hash)
    return {
        "id": str(wl["id"]),
        "ran": True,
        "case_id": str(case_id),
        "changed": changed,
        "new_hash": new_hash,
    }


async def watchlist_tick(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Arq cron entry-point. Schedule via ``cron('* * * * *')``."""
    due = await _load_due()
    results = []
    for wl in due:
        results.append(await run_one(wl))
    return {"checked": len(due), "results": results}
