"""Admin-only DLQ inspection/management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.queueing import dlq
from app.security.middleware import require_admin_token

router = APIRouter(
    prefix="/admin/dlq",
    tags=["admin", "dlq"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("")
async def list_dlq(max: int = Query(100, ge=1, le=1000)) -> dict:
    entries = await dlq.drain(max=max)
    return {"stream": dlq.DLQ_STREAM, "count": len(entries), "entries": entries}


@router.post("/{entry_id}/requeue")
async def requeue_dlq(entry_id: str) -> dict:
    res = await dlq.requeue(entry_id)
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entry not found")
    return {"ok": True, "entry": res}


@router.delete("/{entry_id}")
async def delete_dlq(entry_id: str) -> dict:
    n = await dlq.delete(entry_id)
    if n == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entry not found")
    return {"ok": True, "deleted": n}
