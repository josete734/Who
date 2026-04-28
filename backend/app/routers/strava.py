"""Strava OAuth router (Wave 1 / A1.2)."""
from __future__ import annotations

import datetime as dt
import logging
import re
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator
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


# ---------------------------------------------------------------------------
# Heatmap CloudFront cookie (operator-supplied, stored encrypted globally)
# ---------------------------------------------------------------------------

# A valid CloudFront cookie header must contain all three of these names; the
# Strava heatmap CDN requires the trio together.
_REQUIRED_CF_KEYS = ("CloudFront-Policy", "CloudFront-Signature", "CloudFront-Key-Pair-Id")
# Public probe tile — coords inside Madrid centre, zoom 12, mid-density area.
# Shape: /tiles-auth/global/hot/{z}/{x}/{y}.png
_PROBE_TILE = "https://heatmap-external-a.strava.com/tiles-auth/global/hot/12/2048/1408.png"


class HeatmapCookiePayload(BaseModel):
    cookie: str

    @field_validator("cookie")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        if not v:
            raise ValueError("cookie is empty")
        v = v.strip()
        # Tolerate "Cookie: ..." prefix copy-pasted from devtools.
        if v.lower().startswith("cookie:"):
            v = v.split(":", 1)[1].strip()
        missing = [k for k in _REQUIRED_CF_KEYS if k not in v]
        if missing:
            raise ValueError(f"missing CloudFront keys: {','.join(missing)}")
        # Cookie value must look like a header — 3 segments, each k=v.
        if not re.search(r"CloudFront-Policy=[^;]+", v):
            raise ValueError("CloudFront-Policy value is empty")
        return v


async def _validate_cookie_against_strava(cookie: str) -> tuple[bool, str]:
    """Hit a known authed-tile URL with the cookie. Return (ok, reason)."""
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            headers={
                "Cookie": cookie,
                # CloudFront refuses requests without a browser-shaped UA.
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://www.strava.com/heatmap",
            },
            follow_redirects=False,
        ) as c:
            r = await c.get(_PROBE_TILE)
    except httpx.HTTPError as exc:
        return False, f"network_error: {exc}"
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
        return True, "ok"
    if r.status_code == 200 and len(r.content) > 0:
        # Some edges still serve PNG without explicit content-type; accept.
        return True, "ok_no_ct"
    if r.status_code == 403:
        return False, "cookie_rejected_by_cloudfront"
    if r.status_code == 401:
        return False, "cookie_expired_or_invalid"
    return False, f"unexpected_status: {r.status_code}"


@router.post("/heatmap-cookie")
async def strava_heatmap_cookie_save(payload: HeatmapCookiePayload) -> dict:
    """Persist an operator-supplied CloudFront cookie for the Strava heatmap.

    The cookie is validated against an authed tile on heatmap-external-a, then
    stored ENCRYPTED (Fernet) in ``strava_tokens.cloudfront_cookie`` on the
    most recent global token row (case_id IS NULL). If no global token exists
    yet, a placeholder row is inserted with athlete_id=0 so the cookie can be
    persisted before any OAuth flow has run.
    """
    ok, reason = await _validate_cookie_against_strava(payload.cookie)
    if not ok:
        raise HTTPException(400, f"cookie_validation_failed: {reason}")

    enc = encrypt(payload.cookie)
    now = dt.datetime.now(tz=dt.timezone.utc)

    async with session_scope() as db:
        existing = (
            await db.execute(
                text(
                    "SELECT id FROM strava_tokens WHERE case_id IS NULL "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).mappings().first()
        if existing:
            await db.execute(
                text(
                    "UPDATE strava_tokens SET cloudfront_cookie = :cf, "
                    "cloudfront_cookie_updated_at = :ts WHERE id = :id"
                ),
                {"cf": enc, "ts": now, "id": existing["id"]},
            )
            row_id = existing["id"]
        else:
            # Bootstrap row: athlete_id=0 means "cookie-only, no athlete link".
            row_id = (
                await db.execute(
                    text(
                        "INSERT INTO strava_tokens "
                        "(case_id, athlete_id, access_token_enc, refresh_token_enc, "
                        " expires_at, cloudfront_cookie, cloudfront_cookie_updated_at) "
                        "VALUES (NULL, 0, '', '', :exp, :cf, :ts) "
                        "RETURNING id"
                    ),
                    {"cf": enc, "ts": now, "exp": now},
                )
            ).scalar_one()

    log.info("strava.heatmap_cookie.saved row_id=%s", row_id)
    return {"ok": True, "row_id": int(row_id), "updated_at": now.isoformat()}


@router.delete("/heatmap-cookie")
async def strava_heatmap_cookie_delete() -> dict:
    async with session_scope() as db:
        result = await db.execute(
            text(
                "UPDATE strava_tokens SET cloudfront_cookie = NULL, "
                "cloudfront_cookie_updated_at = NULL "
                "WHERE cloudfront_cookie IS NOT NULL "
                "RETURNING id"
            )
        )
        cleared = result.rowcount or 0
    return {"ok": True, "cleared_rows": cleared}


@router.get("/heatmap-cookie/status")
async def strava_heatmap_cookie_status() -> dict:
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT cloudfront_cookie_updated_at FROM strava_tokens "
                    "WHERE cloudfront_cookie IS NOT NULL "
                    "ORDER BY cloudfront_cookie_updated_at DESC NULLS LAST LIMIT 1"
                )
            )
        ).mappings().first()
    if not row:
        return {"present": False}
    ts = row["cloudfront_cookie_updated_at"]
    return {
        "present": True,
        "updated_at": ts.isoformat() if ts else None,
    }


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
