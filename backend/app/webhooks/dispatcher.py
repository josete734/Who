"""Async webhook fan-out with HMAC signing, retries, and delivery persistence.

Receivers should verify the ``X-Who-Signature`` header by recomputing
``hmac_sha256(body, secret)`` (see :mod:`app.webhooks.signing`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from app.db import session_scope
from app.webhooks.signing import sign

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5  # seconds; 0.5, 1.0, 2.0


async def _load_subs(event: str) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, url, secret, events, enabled FROM webhooks "
                    "WHERE enabled = TRUE"
                )
            )
        ).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        events = r["events"] or []
        if not events or event in events or "*" in events:
            out.append(dict(r))
    return out


async def _record_delivery(
    delivery_id: uuid.UUID,
    webhook_id: uuid.UUID,
    event: str,
    status: str,
    attempts: int,
    last_error: str | None,
    payload: dict[str, Any],
) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "INSERT INTO webhook_deliveries "
                "(id, webhook_id, event, status, attempts, last_error, payload) "
                "VALUES (:id, :wid, :event, :status, :attempts, :err, "
                "CAST(:payload AS JSONB)) "
                "ON CONFLICT (id) DO UPDATE SET "
                "status=EXCLUDED.status, attempts=EXCLUDED.attempts, "
                "last_error=EXCLUDED.last_error"
            ),
            {
                "id": delivery_id,
                "wid": webhook_id,
                "event": event,
                "status": status,
                "attempts": attempts,
                "err": last_error,
                "payload": json.dumps(payload),
            },
        )
        await db.commit()


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[bool, int, str | None]:
    last_err: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = await client.post(url, content=body, headers=headers, timeout=DEFAULT_TIMEOUT)
            if 200 <= r.status_code < 300:
                return True, attempt, None
            last_err = f"http_{r.status_code}: {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"[:300]
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
    return False, MAX_ATTEMPTS, last_err


async def dispatch(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Fan-out ``event`` + ``payload`` to all matching enabled webhooks.

    Returns the list of delivery records (also persisted to
    ``webhook_deliveries``).
    """
    subs = await _load_subs(event)
    if not subs:
        return []

    body_obj = {"event": event, "payload": payload}
    body = json.dumps(body_obj, default=str, sort_keys=True).encode("utf-8")

    async def _one(sub: dict[str, Any]) -> dict[str, Any]:
        delivery_id = uuid.uuid4()
        sig = sign(body, sub["secret"])
        headers = {
            "Content-Type": "application/json",
            "X-Who-Signature": sig,
            "X-Who-Event": event,
            "X-Who-Delivery": str(delivery_id),
        }
        async with httpx.AsyncClient() as client:
            ok, attempts, err = await _post_with_retries(client, sub["url"], body, headers)
        status = "ok" if ok else "error"
        try:
            await _record_delivery(
                delivery_id, sub["id"], event, status, attempts, err, body_obj
            )
        except Exception as e:  # noqa: BLE001
            log.warning("webhook.delivery.persist_failed err=%s", e)
        return {
            "delivery_id": str(delivery_id),
            "webhook_id": str(sub["id"]),
            "status": status,
            "attempts": attempts,
            "error": err,
        }

    return await asyncio.gather(*(_one(s) for s in subs))
