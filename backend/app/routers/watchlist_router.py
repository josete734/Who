"""CRUD for watchlist entries (Wave 4 / D4)."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.db import session_scope
from app.watchlist.model import WatchlistIn, WatchlistOut

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _row(r) -> WatchlistOut:
    return WatchlistOut(
        id=r["id"],
        owner=r["owner"] or "",
        query_inputs=r["query_inputs"] or {},
        schedule_cron=r["schedule_cron"],
        last_run_at=r["last_run_at"],
        last_results_hash=r["last_results_hash"],
        enabled=r["enabled"],
        created_at=r["created_at"],
    )


@router.get("", response_model=list[WatchlistOut])
async def list_watchlist() -> list[WatchlistOut]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, owner, query_inputs, schedule_cron, last_run_at, "
                    "last_results_hash, enabled, created_at "
                    "FROM watchlist ORDER BY created_at DESC"
                )
            )
        ).mappings().all()
    return [_row(r) for r in rows]


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(payload: WatchlistIn) -> WatchlistOut:
    wid = uuid.uuid4()
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "INSERT INTO watchlist (id, owner, query_inputs, schedule_cron, enabled) "
                    "VALUES (:id, :owner, CAST(:qi AS JSONB), :cron, :enabled) "
                    "RETURNING id, owner, query_inputs, schedule_cron, last_run_at, "
                    "last_results_hash, enabled, created_at"
                ),
                {
                    "id": wid,
                    "owner": payload.owner,
                    "qi": json.dumps(payload.query_inputs),
                    "cron": payload.schedule_cron,
                    "enabled": payload.enabled,
                },
            )
        ).mappings().first()
        await db.commit()
    return _row(row)


@router.put("/{wl_id}", response_model=WatchlistOut)
async def update_watchlist(wl_id: uuid.UUID, payload: WatchlistIn) -> WatchlistOut:
    async with session_scope() as db:
        result = await db.execute(
            text(
                "UPDATE watchlist SET owner=:owner, query_inputs=CAST(:qi AS JSONB), "
                "schedule_cron=:cron, enabled=:enabled WHERE id=:id "
                "RETURNING id, owner, query_inputs, schedule_cron, last_run_at, "
                "last_results_hash, enabled, created_at"
            ),
            {
                "id": wl_id,
                "owner": payload.owner,
                "qi": json.dumps(payload.query_inputs),
                "cron": payload.schedule_cron,
                "enabled": payload.enabled,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="watchlist not found")
        await db.commit()
    return _row(row)


@router.delete("/{wl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(wl_id: uuid.UUID) -> None:
    async with session_scope() as db:
        result = await db.execute(
            text("DELETE FROM watchlist WHERE id=:id"), {"id": wl_id}
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="watchlist not found")
        await db.commit()
