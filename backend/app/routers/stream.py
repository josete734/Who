"""SSE endpoint that streams a case's live events."""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.auth import check_auth
from app.event_bus import subscribe

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/cases/{case_id}/stream", dependencies=[Depends(check_auth)])
async def stream_case(case_id: uuid.UUID, request: Request):
    async def event_gen():
        async for evt in subscribe(case_id):
            if await request.is_disconnected():
                break
            yield {
                "event": evt.get("type", "message"),
                "data": json.dumps(evt, default=str),
            }

    return EventSourceResponse(event_gen(), ping=15)
