"""Audit-log read endpoint and right-to-erasure endpoint.

# WIRING ----------------------------------------------------------------
# Auth is being built by Agent A3. Until that lands, the admin-scope
# dependency below is a placeholder that always allows. Once A3 is
# merged, replace `_require_admin` with the real dependency:
#
#       from app.security.scopes import require_scope
#       admin_dep = Depends(require_scope("admin"))
#
# and add `dependencies=[admin_dep]` to the router (or per-route).
#
# Register in main.py:
#       from app.routers.audit_router import router as audit_router
#       app.include_router(audit_router)
# -----------------------------------------------------------------------
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text

from app import audit
from app.db import session_scope

router = APIRouter(tags=["audit"])


# --------------------------------------------------------------------------
# Placeholder admin guard. Replace once Agent A3 ships scope-based auth.
# --------------------------------------------------------------------------
async def _require_admin(request: Request) -> None:
    # WIRING: replace with real admin-scope check from app.security
    return None


@router.get("/api/audit")
async def list_audit(
    request: Request,
    case_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None, max_length=128),
    from_: dt.datetime | None = Query(None, alias="from"),
    to: dt.datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    cursor: int | None = Query(None, ge=0),
    _: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Paginated audit-log read. Admin scope only."""
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if case_id is not None:
        where.append("case_id = :case_id")
        params["case_id"] = str(case_id)
    if action:
        where.append("action = :action")
        params["action"] = action
    if from_ is not None:
        where.append("ts >= :from_ts")
        params["from_ts"] = from_
    if to is not None:
        where.append("ts <= :to_ts")
        params["to_ts"] = to
    if cursor is not None:
        where.append("id < :cursor")
        params["cursor"] = cursor

    sql = "SELECT id, ts, actor_api_key_id, action, case_id, target, metadata, ip, user_agent FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT :limit"

    async with session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()

    items = [dict(r) for r in rows]
    next_cursor = items[-1]["id"] if len(items) == limit else None

    # Reading the audit log is itself an auditable event.
    await audit.record(
        "audit.read",
        metadata={"count": len(items), "filters": {k: str(v) for k, v in params.items() if k != "limit"}},
        request=request,
    )

    return {"items": items, "next_cursor": next_cursor}


# --------------------------------------------------------------------------
# Right-to-erasure (GDPR Art. 17)
# --------------------------------------------------------------------------
def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


@router.post("/api/cases/{case_id}/forget", status_code=status.HTTP_200_OK)
async def forget_case(
    case_id: uuid.UUID,
    request: Request,
    _: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Pseudonymize all findings for a case.

    Replaces PII-bearing columns (title, url, payload) with deterministic
    hashes so aggregate stats / counts are preserved while personal data
    is removed. The case row itself is kept (audit linkage); its
    ``input_payload`` is also pseudonymised.
    """
    async with session_scope() as s:
        case_row = (
            await s.execute(text("SELECT id FROM cases WHERE id = :id"), {"id": str(case_id)})
        ).first()
        if case_row is None:
            raise HTTPException(status_code=404, detail="case_not_found")

        # Findings: hash title/url, replace payload with {"_pseudonymised": true, "h": ...}
        findings = (
            await s.execute(
                text("SELECT id, title, url FROM findings WHERE case_id = :cid"),
                {"cid": str(case_id)},
            )
        ).mappings().all()

        for f in findings:
            await s.execute(
                text(
                    """
                    UPDATE findings
                       SET title = :title,
                           url   = NULL,
                           payload = CAST(:payload AS JSONB)
                     WHERE id = :id
                    """
                ),
                {
                    "id": f["id"],
                    "title": _hash(f["title"] or ""),
                    "payload": '{"_pseudonymised": true, "h": "'
                    + _hash((f["title"] or "") + "|" + (f["url"] or ""))
                    + '"}',
                },
            )

        # Pseudonymise the case input payload too.
        await s.execute(
            text(
                """
                UPDATE cases
                   SET input_payload = CAST('{"_pseudonymised": true}' AS JSONB),
                       title = :ptitle
                 WHERE id = :id
                """
            ),
            {"id": str(case_id), "ptitle": _hash(str(case_id))},
        )

    await audit.record(
        "case.forgotten",
        case_id=case_id,
        metadata={"findings_pseudonymised": len(findings)},
        request=request,
    )

    return {"ok": True, "case_id": str(case_id), "findings_pseudonymised": len(findings)}
