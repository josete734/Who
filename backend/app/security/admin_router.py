"""Admin endpoints for API key lifecycle.

All endpoints require Authorization: Bearer <ADMIN_TOKEN>.
If the ADMIN_TOKEN env var is unset, every admin call returns 503 (fail closed).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.db import session_scope
from app.security import api_keys as ak
from app.security.middleware import require_admin_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/keys", tags=["admin"])


class CreateKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)


class KeyMetaOut(BaseModel):
    id: uuid.UUID
    name: str
    scopes: list[str]
    rate_limit_per_minute: int
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    @classmethod
    def from_row(cls, row: ak.ApiKey) -> "KeyMetaOut":
        return cls(
            id=row.id,
            name=row.name,
            scopes=list(row.scopes or []),
            rate_limit_per_minute=row.rate_limit_per_minute,
            created_at=row.created_at.isoformat() if row.created_at else "",
            last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
            revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
        )


class CreateKeyOut(KeyMetaOut):
    token: str  # plaintext, shown once


@router.post("", response_model=CreateKeyOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin_token)])
async def create_key(payload: CreateKeyIn) -> CreateKeyOut:
    async with session_scope() as session:
        row, token = await ak.create_api_key(
            session,
            name=payload.name,
            scopes=payload.scopes,
            rate_limit_per_minute=payload.rate_limit_per_minute,
        )
        meta = KeyMetaOut.from_row(row)
    return CreateKeyOut(**meta.model_dump(), token=token)


@router.get("", response_model=list[KeyMetaOut],
            dependencies=[Depends(require_admin_token)])
async def list_keys() -> list[KeyMetaOut]:
    async with session_scope() as session:
        rows = await ak.list_api_keys(session)
        return [KeyMetaOut.from_row(r) for r in rows]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin_token)])
async def revoke_key(key_id: uuid.UUID) -> None:
    async with session_scope() as session:
        ok = await ak.revoke_api_key(session, key_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    logger.info("api_key.revoked id=%s", key_id)
