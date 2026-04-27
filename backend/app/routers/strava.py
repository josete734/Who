"""Strava OAuth router (Wave 1 / A1.2)."""
from __future__ import annotations

import datetime as dt
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import session_scope
from app.integrations.strava_oauth import (
    build_authorize_url,
    encrypt,
    ensure_case_token_link,
    exchange_code,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/strava", tags=["strava"])


@router.get("/oauth/start")
async def strava_oauth_start(case_id: str = Query(..., min_length=1)) -> RedirectResponse:
    s = get_settings()
    if not s.strava_client_id:
        raise HTTPException(503, "strava_client_id_not_configured")
    url = build_authorize_url(
        case_id=case_id,
        client_id=s.strava_client_id,
        redirect_uri=s.strava_redirect_uri,
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/callback", response_class=HTMLResponse)
async def strava_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
) -> HTMLResponse:
    if error:
        return HTMLResponse(
            f"<h1>Strava OAuth error</h1><pre>{error}</pre>", status_code=400
        )
    s = get_settings()
    if not s.strava_client_id or not s.strava_client_secret:
        raise HTTPException(503, "strava_oauth_not_configured")

    try:
        case_id = uuid.UUID(state)
    except ValueError as exc:
        raise HTTPException(400, "invalid_state") from exc

    try:
        tok = await exchange_code(code, s.strava_client_id, s.strava_client_secret)
    except Exception as exc:  # noqa: BLE001
        log.warning("strava.exchange_failed err=%s", exc)
        raise HTTPException(502, "strava_token_exchange_failed") from exc

    athlete = tok.get("athlete") or {}
    athlete_id = int(athlete.get("id") or 0)
    expires_at = dt.datetime.fromtimestamp(
        int(tok.get("expires_at") or 0), tz=dt.timezone.utc
    )

    at_enc = encrypt(tok.get("access_token") or "")
    rt_enc = encrypt(tok.get("refresh_token") or "")

    # WIRING: persist the token GLOBALLY (case_id IS NULL) keyed by
    # athlete_id so future cases without an explicit Strava link can
    # still authenticate — UPSERT on the partial unique index
    # uniq_strava_tokens_athlete (athlete_id WHERE case_id IS NULL).
    log.info(
        "strava.callback received state=%s athlete_id=%s — persisting global token",
        case_id,
        athlete_id,
    )
    async with session_scope() as db:
        existing_global = (
            await db.execute(
                text(
                    "SELECT id FROM strava_tokens "
                    "WHERE athlete_id = :aid AND case_id IS NULL "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"aid": athlete_id},
            )
        ).mappings().first()
        if existing_global:
            await db.execute(
                text(
                    "UPDATE strava_tokens "
                    "SET access_token_enc = :at, refresh_token_enc = :rt, "
                    "    expires_at = :exp "
                    "WHERE id = :id"
                ),
                {
                    "at": at_enc,
                    "rt": rt_enc,
                    "exp": expires_at,
                    "id": existing_global["id"],
                },
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO strava_tokens "
                    "(case_id, athlete_id, access_token_enc, refresh_token_enc, expires_at) "
                    "VALUES (NULL, :aid, :at, :rt, :exp)"
                ),
                {
                    "aid": athlete_id,
                    "at": at_enc,
                    "rt": rt_enc,
                    "exp": expires_at,
                },
            )

    # Idempotent legacy clone: also bind the token to the case_id from state
    # so existing demos / cases that select by case_id keep working.
    try:
        await ensure_case_token_link(case_id, athlete_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("strava.callback.case_link_failed err=%s", exc)

    name = (
        f"{athlete.get('firstname') or ''} {athlete.get('lastname') or ''}".strip()
        or athlete.get("username")
        or str(athlete_id)
    )
    body = f"""
    <html><body style="font-family:system-ui;margin:2em">
      <h1>Strava vinculado</h1>
      <p>Atleta: <strong>{name}</strong> (id {athlete_id})</p>
      <p>Caso: <code>{case_id}</code></p>
      <p>Token expira: {expires_at.isoformat()}</p>
    </body></html>
    """
    return HTMLResponse(body)


@router.get("/link/{case_id}")
async def strava_link_status(case_id: uuid.UUID) -> dict:
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT athlete_id, expires_at, created_at "
                    "FROM strava_tokens WHERE case_id = :cid "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"cid": str(case_id)},
            )
        ).mappings().first()
    if not row:
        return {"linked": False, "case_id": str(case_id)}
    return {
        "linked": True,
        "case_id": str(case_id),
        "athlete_id": row["athlete_id"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
