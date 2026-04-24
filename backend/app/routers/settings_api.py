"""/api/settings endpoints for reading/writing editable runtime config."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.dynamic_settings import EDITABLE_KEYS, SECRET_KEYS, get_runtime, put_many, redact
from app.db import AuditLog, session_scope

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, str]


@router.get("/settings")
async def list_settings() -> dict:
    runtime = await get_runtime()
    return {
        "keys": [
            {
                "key": k,
                "value": redact(k, runtime.get(k, "")),
                "has_value": bool(runtime.get(k)),
                "is_secret": k in SECRET_KEYS,
            }
            for k in EDITABLE_KEYS
        ]
    }


@router.post("/settings")
async def update_settings(body: SettingsUpdate, request: Request) -> dict:
    # Ignore redacted values that come back from the UI unchanged
    filtered = {k: v for k, v in body.values.items() if "•" not in (v or "")}
    await put_many(filtered)
    async with session_scope() as s:
        s.add(AuditLog(
            event="settings_updated",
            actor_ip=(request.client.host if request.client else None),
            payload={"keys": list(filtered.keys())},
        ))
    runtime = await get_runtime()
    return {
        "ok": True,
        "updated": list(filtered.keys()),
        "keys": [
            {"key": k, "value": redact(k, runtime.get(k, "")), "has_value": bool(runtime.get(k)), "is_secret": k in SECRET_KEYS}
            for k in EDITABLE_KEYS
        ],
    }
