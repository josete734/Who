"""SSE router for the autonomous investigator.

Wave 3 — the previous Agent A8 stub has been replaced by
``LiveCollectorDispatcher`` (see app.ai_investigator.collector_dispatcher).
The dependency injection seam remains so tests can inject fakes; production
defaults to the live dispatcher backed by the real DB + pivot dispatcher.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai_investigator.collector_dispatcher import LiveCollectorDispatcher
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


async def get_dispatcher() -> CollectorDispatcher:
    """Production dispatcher. Tests can override this via FastAPI's
    ``app.dependency_overrides`` to inject a fake implementation."""
    return LiveCollectorDispatcher()  # type: ignore[return-value]


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
