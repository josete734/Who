"""Export endpoints (PDF / STIX 2.1 / MISP) for finalized cases."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from app.auth import check_auth
from app.db import session_scope
from app.exporters import export_misp, export_pdf, export_stix

# WIRING: include in app.main with `app.include_router(export_router.router)`
router = APIRouter(prefix="/api/cases", tags=["export"])


@router.get("/{case_id}/export/pdf", dependencies=[Depends(check_auth)])
async def export_case_pdf(case_id: uuid.UUID) -> Response:
    try:
        async with session_scope() as db:
            data = await export_pdf(case_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="case-{case_id}.pdf"'},
    )


@router.get("/{case_id}/export/stix", dependencies=[Depends(check_auth)])
async def export_case_stix(case_id: uuid.UUID) -> JSONResponse:
    try:
        async with session_scope() as db:
            bundle = await export_stix(case_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse(content=bundle)


@router.get("/{case_id}/export/misp", dependencies=[Depends(check_auth)])
async def export_case_misp(case_id: uuid.UUID) -> JSONResponse:
    try:
        async with session_scope() as db:
            event = await export_misp(case_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse(content=event)
