"""SSE router for the autonomous investigator.

NOT wired into main.py yet — the dispatcher (Agent A8) must land first. To
enable, register with `app.include_router(investigator_router.router)` and
provide a `CollectorDispatcher` via dependency injection.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai_investigator.runner import (
    CollectorDispatcher,
    InvestigatorRunner,
)
from app.auth import check_auth

router = APIRouter(prefix="/api/cases", tags=["investigator"])


class InvestigateRequest(BaseModel):
    provider: str | None = Field(
        default=None, description="claude | openai | gemini. Defaults to settings.DEFAULT_LLM."
    )
    max_steps: int = Field(default=8, ge=1, le=32)
    language: str = Field(default="es")
    case_brief: str | None = None


async def get_dispatcher() -> CollectorDispatcher:  # pragma: no cover - DI seam
    """Override this with the real Agent A8 dispatcher when wiring in main.py."""
    raise HTTPException(
        status_code=503,
        detail="CollectorDispatcher not wired (waiting on Agent A8)",
    )


@router.post(
    "/{case_id}/investigate",
    dependencies=[Depends(check_auth)],
)
async def investigate(
    case_id: uuid.UUID,
    req: InvestigateRequest,
    dispatcher: CollectorDispatcher = Depends(get_dispatcher),
) -> StreamingResponse:
    runner = await InvestigatorRunner.from_settings(
        case_id=case_id,
        dispatcher=dispatcher,
        max_steps=req.max_steps,
        provider=req.provider,
        language=req.language,
        case_brief=req.case_brief,
    )

    async def _stream() -> Any:
        async for ev in runner.run():
            yield ev.to_sse()

    return StreamingResponse(_stream(), media_type="text/event-stream")
