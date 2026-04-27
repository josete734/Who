"""Append-only audit log writer.

Single entry point: ``await audit.record(action, ...)``.

Design constraints:
    * Never raises. A failed audit insert must NOT break the request path.
    * Single INSERT, no UPDATE / DELETE (the table also blocks them, see
      migration 0001_audit_log.sql).
    * Best-effort extraction of IP / User-Agent from a FastAPI Request.

Wired into routers by Agent A3 (auth) and the cases router (A?).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.db import session_scope

log = logging.getLogger(__name__)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Respect X-Forwarded-For when behind Caddy / reverse proxy.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.headers.get("user-agent")


async def record(
    action: str,
    *,
    case_id: uuid.UUID | str | None = None,
    target: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
    actor_api_key_id: uuid.UUID | str | None = None,
) -> None:
    """Insert a single audit_log row. Never raises.

    Parameters
    ----------
    action:
        Dotted event name, e.g. ``case.created``, ``case.forgotten``,
        ``audit.read``, ``finding.exported``.
    case_id, target, metadata:
        Optional context. ``target`` is the subject of the action
        (e.g. ``{"email": "..."}``); ``metadata`` is request context.
    request:
        FastAPI ``Request``; IP and UA are extracted from it.
    actor_api_key_id:
        Auth subject (passed by the auth dependency once A3 lands).
    """
    try:
        async with session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO audit_log
                        (actor_api_key_id, action, case_id,
                         target, metadata, ip, user_agent)
                    VALUES
                        (:actor, :action, :case_id,
                         CAST(:target AS JSONB), CAST(:metadata AS JSONB),
                         CAST(:ip AS INET), :ua)
                    """
                ),
                {
                    "actor": str(actor_api_key_id) if actor_api_key_id else None,
                    "action": action,
                    "case_id": str(case_id) if case_id else None,
                    "target": _json(target),
                    "metadata": _json(metadata),
                    "ip": _client_ip(request),
                    "ua": _user_agent(request),
                },
            )
    except Exception as exc:  # noqa: BLE001 - by design
        log.warning("audit.record failed for action=%s: %s", action, exc)


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    import json

    try:
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return None
