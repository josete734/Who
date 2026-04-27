"""Pattern-miner router (NOT wired into main.py).

Exposes POST /api/cases/{id}/mine_patterns, returning verified candidates.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import check_auth
from app.collectors import collector_registry
from app.db import Case, Finding, session_scope
from app.pattern_miner.miner import mine_patterns

router = APIRouter(prefix="/api", tags=["pattern_miner"])


class MineRequest(BaseModel):
    domains: list[str] = Field(default_factory=list)
    enable_network: bool = True
    persist: bool = True
    max_username_variants: int = 120
    max_email_variants: int = 120


class MineResponse(BaseModel):
    case_id: str
    usernames: list[dict]
    emails: list[dict]


@router.post("/cases/{case_id}/mine_patterns",
             response_model=MineResponse,
             dependencies=[Depends(check_auth)])
async def mine_patterns_endpoint(case_id: uuid.UUID, req: MineRequest) -> MineResponse:
    async with session_scope() as s:
        case = await s.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        ip = case.input_payload or {}
        # Collect BORME payloads to auto-detect company domains.
        borme_rows = (await s.execute(
            select(Finding.payload).where(
                Finding.case_id == case_id,
                Finding.collector == "borme",
            )
        )).scalars().all()
        borme_payloads = [p for p in borme_rows if isinstance(p, dict)]

    result = await mine_patterns(
        case_id=case_id,
        full_name=ip.get("full_name"),
        birth_name=ip.get("birth_name"),
        aliases=ip.get("aliases"),
        domains=req.domains,
        borme_payloads=borme_payloads,
        collector_registry=collector_registry,
        persist=req.persist,
        enable_network=req.enable_network,
        max_username_variants=req.max_username_variants,
        max_email_variants=req.max_email_variants,
    )
    data = result.to_dict()
    return MineResponse(
        case_id=str(case_id),
        usernames=[u for u in data["usernames"] if u["verified"]],
        emails=[e for e in data["emails"] if e["verified"]],
    )
