"""REST endpoints for cases and findings."""
from __future__ import annotations

import datetime as dt
import uuid

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select

from app.auth import check_auth
from app.collectors import collector_registry
from app.db import AuditLog, Case, CollectorRun, Finding, session_scope
from app.schemas import CaseOut, CollectorRunOut, FindingOut, NewCaseRequest
from app.tasks import WorkerSettings

router = APIRouter(prefix="/api", tags=["cases"])


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "collectors": [c.name for c in collector_registry.all()]}


@router.post("/cases", dependencies=[Depends(check_auth)])
async def create_case(req: NewCaseRequest, request: Request) -> dict:
    case_id = uuid.uuid4()
    non_empty = req.input.non_empty_fields()
    if not non_empty:
        raise HTTPException(400, "input vacío: proporciona al menos un campo")

    async with session_scope() as s:
        s.add(Case(
            id=case_id,
            title=req.title,
            legal_basis=req.legal_basis,
            input_payload=non_empty,
            status="queued",
        ))
        s.add(AuditLog(
            event="case_created",
            actor_ip=(request.client.host if request.client else None),
            payload={"case_id": str(case_id), "input": non_empty, "llm": req.llm},
        ))

    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        await pool.enqueue_job(
            "run_case_task",
            str(case_id),
            req.input.model_dump(exclude_none=True),
            req.llm,
        )
    finally:
        await pool.close()

    return {"case_id": str(case_id), "status": "queued", "llm": req.llm}


@router.get("/cases", response_model=list[CaseOut], dependencies=[Depends(check_auth)])
async def list_cases() -> list[CaseOut]:
    async with session_scope() as s:
        cases = (await s.execute(select(Case).order_by(Case.created_at.desc()).limit(100))).scalars().all()
    return [CaseOut.model_validate(c, from_attributes=True) for c in cases]


@router.get("/cases/{case_id}", response_model=CaseOut, dependencies=[Depends(check_auth)])
async def get_case(case_id: uuid.UUID) -> CaseOut:
    async with session_scope() as s:
        case = (await s.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
        if not case:
            raise HTTPException(404)
    return CaseOut.model_validate(case, from_attributes=True)


@router.get("/cases/{case_id}/findings", response_model=list[FindingOut], dependencies=[Depends(check_auth)])
async def case_findings(case_id: uuid.UUID) -> list[FindingOut]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Finding).where(Finding.case_id == case_id).order_by(Finding.created_at)
            )
        ).scalars().all()
    return [FindingOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/cases/{case_id}/collectors", response_model=list[CollectorRunOut], dependencies=[Depends(check_auth)])
async def case_collectors(case_id: uuid.UUID) -> list[CollectorRunOut]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(CollectorRun).where(CollectorRun.case_id == case_id).order_by(CollectorRun.started_at)
            )
        ).scalars().all()
    return [CollectorRunOut.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/cases/{case_id}", dependencies=[Depends(check_auth)])
async def delete_case(case_id: uuid.UUID) -> dict:
    async with session_scope() as s:
        await s.execute(delete(Finding).where(Finding.case_id == case_id))
        await s.execute(delete(CollectorRun).where(CollectorRun.case_id == case_id))
        await s.execute(delete(Case).where(Case.id == case_id))
    return {"ok": True}
