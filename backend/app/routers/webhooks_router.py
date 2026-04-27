"""CRUD for webhook subscriptions (Wave 4 / D4)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.db import session_scope
from app.webhooks.model import WebhookIn, WebhookOut

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookOut])
async def list_webhooks() -> list[WebhookOut]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, url, events, enabled, created_at "
                    "FROM webhooks ORDER BY created_at DESC"
                )
            )
        ).mappings().all()
    return [
        WebhookOut(
            id=r["id"],
            url=r["url"],
            events=r["events"] or [],
            enabled=r["enabled"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(payload: WebhookIn) -> WebhookOut:
    wid = uuid.uuid4()
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "INSERT INTO webhooks (id, url, secret, events, enabled) "
                    "VALUES (:id, :url, :secret, CAST(:events AS JSONB), :enabled) "
                    "RETURNING id, url, events, enabled, created_at"
                ),
                {
                    "id": wid,
                    "url": str(payload.url),
                    "secret": payload.secret,
                    "events": json.dumps(payload.events),
                    "enabled": payload.enabled,
                },
            )
        ).mappings().first()
        await db.commit()
    return WebhookOut(
        id=row["id"],
        url=row["url"],
        events=row["events"] or [],
        enabled=row["enabled"],
        created_at=row["created_at"],
    )


@router.put("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(webhook_id: uuid.UUID, payload: WebhookIn) -> WebhookOut:
    async with session_scope() as db:
        result = await db.execute(
            text(
                "UPDATE webhooks SET url=:url, secret=:secret, "
                "events=CAST(:events AS JSONB), enabled=:enabled WHERE id=:id "
                "RETURNING id, url, events, enabled, created_at"
            ),
            {
                "id": webhook_id,
                "url": str(payload.url),
                "secret": payload.secret,
                "events": json.dumps(payload.events),
                "enabled": payload.enabled,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="webhook not found")
        await db.commit()
    return WebhookOut(
        id=row["id"],
        url=row["url"],
        events=row["events"] or [],
        enabled=row["enabled"],
        created_at=row["created_at"],
    )


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: uuid.UUID) -> None:
    async with session_scope() as db:
        result = await db.execute(
            text("DELETE FROM webhooks WHERE id=:id"), {"id": webhook_id}
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="webhook not found")
        await db.commit()


@router.get("/{webhook_id}/deliveries")
async def list_deliveries(webhook_id: uuid.UUID, limit: int = 100) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, event, status, attempts, last_error, created_at "
                    "FROM webhook_deliveries WHERE webhook_id=:wid "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"wid": webhook_id, "limit": limit},
            )
        ).mappings().all()
    return [dict(r) for r in rows]
