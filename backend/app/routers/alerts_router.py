"""Alerts list + ack endpoints.

# WIRING (NOT done in this PR) -----------------------------------------
#   from app.routers.alerts_router import router as alerts_router
#   app.include_router(alerts_router)
# ----------------------------------------------------------------------
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from app.db import session_scope

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    level: str
    message: str
    payload: dict[str, Any]
    acked_at: dt.datetime | None
    created_at: dt.datetime


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    case_id: uuid.UUID | None = Query(None),
    level: str | None = Query(None, max_length=32),
    unacked: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
) -> list[AlertOut]:
    sql = (
        "SELECT id, case_id, rule_id, level, message, payload, acked_at, created_at "
        "FROM alerts WHERE 1=1"
    )
    params: dict[str, Any] = {"limit": limit}
    if case_id is not None:
        sql += " AND case_id = :case_id"
        params["case_id"] = case_id
    if level is not None:
        sql += " AND level = :level"
        params["level"] = level
    if unacked:
        sql += " AND acked_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT :limit"

    async with session_scope() as db:
        rows = (await db.execute(text(sql), params)).mappings().all()
    return [
        AlertOut(
            id=r["id"],
            case_id=r["case_id"],
            rule_id=r["rule_id"],
            level=r["level"],
            message=r["message"],
            payload=r["payload"] or {},
            acked_at=r["acked_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{alert_id}/ack", status_code=status.HTTP_200_OK)
async def ack_alert(alert_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as db:
        result = await db.execute(
            text("UPDATE alerts SET acked_at = now() WHERE id = :id AND acked_at IS NULL"),
            {"id": alert_id},
        )
        if result.rowcount == 0:
            # Either missing or already acked.
            row = (
                await db.execute(
                    text("SELECT id, acked_at FROM alerts WHERE id = :id"),
                    {"id": alert_id},
                )
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="alert not found")
            await db.commit()
            return {"id": str(alert_id), "acked": True, "already": True}
        await db.commit()
    return {"id": str(alert_id), "acked": True}
