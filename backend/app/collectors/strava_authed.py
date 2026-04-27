"""Authenticated Strava collector (Wave 1 / A1.2).

Uses the OAuth tokens persisted by ``app.routers.strava`` to fetch the
athlete's recent activities including summary polylines. Falls back silently
when no token is linked for the case.
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import json
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
from sqlalchemy import text

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.db import session_scope
from app.integrations.strava_oauth import decrypt, encrypt, refresh_token
from app.netfetch import get_client
from app.schemas import SearchInput

log = logging.getLogger(__name__)

ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
MAX_PAGES = 5
PER_PAGE = 200

# Context var the orchestrator can set so collectors learn the active case_id
# without forcing it through the SearchInput schema.
current_case_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "strava_current_case_id", default=None
)


def _resolve_case_id(input: SearchInput) -> uuid.UUID | None:
    cid = current_case_id.get()
    if cid:
        try:
            return uuid.UUID(cid)
        except (ValueError, TypeError):
            pass
    raw = input.extra_context
    if isinstance(raw, dict):
        cand = raw.get("case_id")
        if cand:
            try:
                return uuid.UUID(str(cand))
            except (ValueError, TypeError):
                return None
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            data = json.loads(raw)
            cand = data.get("case_id") if isinstance(data, dict) else None
            if cand:
                return uuid.UUID(str(cand))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
    return None


async def _load_token(
    case_id: uuid.UUID | None, athlete_id: int | None = None
) -> dict | None:
    """Load a Strava OAuth token row.

    Priority:
      1. Global token: athlete_id matches AND case_id IS NULL.
      2. Legacy fallback: case_id matches.
    """
    async with session_scope() as db:
        row = None
        if athlete_id is not None:
            row = (
                await db.execute(
                    text(
                        "SELECT id, athlete_id, access_token_enc, "
                        "refresh_token_enc, expires_at FROM strava_tokens "
                        "WHERE athlete_id = :aid AND case_id IS NULL "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"aid": int(athlete_id)},
                )
            ).mappings().first()
        if row is None and case_id is not None:
            row = (
                await db.execute(
                    text(
                        "SELECT id, athlete_id, access_token_enc, "
                        "refresh_token_enc, expires_at FROM strava_tokens "
                        "WHERE case_id = :cid "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().first()
    if not row:
        return None
    return {
        "id": row["id"],
        "athlete_id": row["athlete_id"],
        "access_token": decrypt(row["access_token_enc"]),
        "refresh_token": decrypt(row["refresh_token_enc"]),
        "expires_at": row["expires_at"],
    }


async def _athlete_id_from_findings(case_id: uuid.UUID) -> int | None:
    """Look up athlete_id persisted by strava_public earlier in the run."""
    try:
        async with session_scope() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT payload->>'athlete_id' AS aid FROM findings "
                        "WHERE case_id = :cid AND collector = 'strava_public' "
                        "AND payload ? 'athlete_id' "
                        "AND payload->>'athlete_id' IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"cid": str(case_id)},
                )
            ).mappings().first()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row.get("aid"):
        return None
    try:
        return int(row["aid"])
    except (ValueError, TypeError):
        return None


async def _persist_refresh(token_row_id: int, payload: dict) -> dict:
    new_access = payload.get("access_token") or ""
    new_refresh = payload.get("refresh_token") or ""
    new_exp = dt.datetime.fromtimestamp(
        int(payload.get("expires_at") or 0), tz=dt.timezone.utc
    )
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE strava_tokens SET access_token_enc=:at, "
                "refresh_token_enc=:rt, expires_at=:exp WHERE id=:id"
            ),
            {
                "at": encrypt(new_access),
                "rt": encrypt(new_refresh),
                "exp": new_exp,
                "id": token_row_id,
            },
        )
    return {"access_token": new_access, "refresh_token": new_refresh, "expires_at": new_exp}


@register
class StravaAuthedCollector(Collector):
    name = "strava_authed"
    category = "sport"
    needs = ("username", "full_name", "email", "city")
    timeout_seconds = 120

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        case_id = _resolve_case_id(input)

        # Discover athlete_id: first via findings persisted by strava_public,
        # then via input.username if numeric. strava_public runs in parallel
        # so we retry briefly to cover the race.
        athlete_id: int | None = None
        uname = (input.username or "").lstrip("@").strip()
        if uname.isdigit():
            try:
                athlete_id = int(uname)
            except ValueError:
                athlete_id = None

        if athlete_id is None and case_id is not None:
            for _ in range(60):  # poll up to ~60s for strava_public
                athlete_id = await _athlete_id_from_findings(case_id)
                if athlete_id is not None:
                    break
                await asyncio.sleep(1)

        if athlete_id is None and case_id is None:
            return

        # Backward-compat: legacy tests/monkeypatches expect _load_token(cid).
        # Only pass the athlete_id kwarg when we actually resolved one.
        if athlete_id is not None:
            tok = await _load_token(case_id, athlete_id=athlete_id)
        else:
            tok = await _load_token(case_id)
        if not tok:
            return

        s = get_settings()
        now = dt.datetime.now(dt.timezone.utc)
        access = tok["access_token"]
        if tok["expires_at"] and tok["expires_at"] < now:
            if not (s.strava_client_id and s.strava_client_secret):
                return
            try:
                refreshed = await refresh_token(
                    tok["refresh_token"], s.strava_client_id, s.strava_client_secret
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("strava_authed.refresh_failed err=%s", exc)
                return
            new = await _persist_refresh(tok["id"], refreshed)
            access = new["access_token"]

        headers = {"Authorization": f"Bearer {access}"}

        async with await get_client("gentle") as c:
            for page in range(1, MAX_PAGES + 1):
                try:
                    r = await c.get(
                        ACTIVITIES_URL,
                        params={"per_page": PER_PAGE, "page": page},
                        headers=headers,
                        timeout=20.0,
                    )
                except (httpx.HTTPError, OSError) as exc:
                    log.warning("strava_authed.http_error page=%s err=%s", page, exc)
                    return
                if r.status_code == 401:
                    log.info("strava_authed.unauthorized page=%s", page)
                    return
                if r.status_code != 200:
                    log.warning(
                        "strava_authed.bad_status page=%s status=%s",
                        page,
                        r.status_code,
                    )
                    return
                try:
                    items = r.json()
                except (ValueError, json.JSONDecodeError):
                    items = []
                if not isinstance(items, list) or not items:
                    return

                for act in items:
                    if not isinstance(act, dict):
                        continue
                    act_map = act.get("map") or {}
                    photos = act.get("photos") or {}
                    primary = photos.get("primary") if isinstance(photos, dict) else None
                    photo_url = None
                    if isinstance(primary, dict):
                        urls = primary.get("urls") or {}
                        if isinstance(urls, dict):
                            photo_url = urls.get("600") or urls.get("100")
                    start_latlng = act.get("start_latlng") or None
                    end_latlng = act.get("end_latlng") or None
                    flagged = bool(act.get("flagged") or act.get("private"))
                    privacy_zone = (not start_latlng) or flagged

                    payload = {
                        "activity_id": act.get("id"),
                        "name": act.get("name"),
                        "sport_type": act.get("sport_type") or act.get("type"),
                        "start_date_local": act.get("start_date_local"),
                        "distance": act.get("distance"),
                        "moving_time": act.get("moving_time"),
                        "polyline": act_map.get("summary_polyline"),
                        "start_latlng": start_latlng,
                        "end_latlng": end_latlng,
                        "photo_url": photo_url,
                        "privacy_zone": privacy_zone,
                    }
                    yield Finding(
                        collector=self.name,
                        category="sport",
                        entity_type="activity",
                        title=f"Strava: {payload['name'] or payload['activity_id']}",
                        url=(
                            f"https://www.strava.com/activities/{act.get('id')}"
                            if act.get("id")
                            else None
                        ),
                        confidence=0.9,
                        payload=payload,
                    )

                if len(items) < PER_PAGE:
                    return
                # Respect rate limits (100 / 15min): pace pages.
                await asyncio.sleep(0.5)
