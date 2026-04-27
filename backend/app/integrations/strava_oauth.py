"""Strava OAuth helpers (Wave 1 / A1.2).

Implements authorize URL building, code exchange, refresh, and Fernet-based
token encryption. All HTTP traffic uses the shared httpx client with the
``gentle`` host policy to respect Strava rate limits.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid
from typing import Any
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.config import get_settings
from app.netfetch import get_client

log = logging.getLogger(__name__)

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def build_authorize_url(
    case_id: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "read,activity:read_all",
) -> str:
    """Build the Strava OAuth authorize URL with ``state=case_id``."""
    qs = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": scope,
            "state": case_id,
        }
    )
    return f"{STRAVA_AUTH_URL}?{qs}"


async def exchange_code(code: str, client_id: str, client_secret: str) -> dict[str, Any]:
    """Exchange an authorization code for access/refresh tokens."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    async with await get_client("gentle") as c:
        r = await c.post(STRAVA_TOKEN_URL, data=payload, timeout=20.0)
        r.raise_for_status()
        return r.json()


async def refresh_token(
    refresh_token: str, client_id: str, client_secret: str
) -> dict[str, Any]:
    """Refresh an expired access token."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    async with await get_client("gentle") as c:
        r = await c.post(STRAVA_TOKEN_URL, data=payload, timeout=20.0)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Token encryption (Fernet)
# ---------------------------------------------------------------------------


def _derive_key() -> bytes:
    """Return a 32-byte url-safe-base64 Fernet key.

    Uses ``settings.strava_encryption_key`` if set; otherwise derives a stable
    key by HMAC-SHA256(``settings.session_secret`` or ``settings.auth_token``,
    ``"strava-oauth"``).
    """
    s = get_settings()
    raw = (getattr(s, "strava_encryption_key", "") or "").strip()
    if raw:
        # Accept either an already-base64 Fernet key or arbitrary text we hash.
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            if len(decoded) == 32:
                return raw.encode("ascii")
        except (ValueError, TypeError):
            pass
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    seed = (
        getattr(s, "session_secret", "")
        or getattr(s, "auth_token", "")
        or "osint-fallback-secret"
    )
    mac = hmac.new(seed.encode("utf-8"), b"strava-oauth", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac)


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt(plain: str) -> str:
    if plain is None:
        plain = ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(blob: str) -> str:
    return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Case <-> athlete token linking
# ---------------------------------------------------------------------------


async def ensure_case_token_link(
    case_id: "uuid.UUID | str", athlete_id: int
) -> bool:
    """Clone a global (case_id IS NULL) Strava token row onto a specific case.

    Fail-soft: on any DB error returns False without raising. Idempotent:
    if a row already exists for (case_id, athlete_id), no-ops via
    ON CONFLICT DO NOTHING.

    # WIRING: invoked by orchestrator._run_one after strava_public yields
    # a finding with payload.athlete_id, so strava_authed (which still
    # selects by case_id) can find the inherited token.
    """
    try:
        from app.db import session_scope
    except Exception:  # noqa: BLE001
        return False
    try:
        aid_int = int(athlete_id)
    except (ValueError, TypeError):
        return False
    try:
        async with session_scope() as s:
            global_row = (
                await s.execute(
                    text(
                        "SELECT access_token_enc, refresh_token_enc, expires_at "
                        "FROM strava_tokens "
                        "WHERE athlete_id = :a AND case_id IS NULL "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"a": aid_int},
                )
            ).mappings().first()
            if not global_row:
                return False
            await s.execute(
                text(
                    "INSERT INTO strava_tokens "
                    "(case_id, athlete_id, access_token_enc, refresh_token_enc, "
                    " expires_at, created_at) "
                    "VALUES (:c, :a, :at, :rt, :e, now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "c": str(case_id),
                    "a": aid_int,
                    "at": global_row["access_token_enc"],
                    "rt": global_row["refresh_token_enc"],
                    "e": global_row["expires_at"],
                },
            )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "strava_oauth.ensure_case_token_link_failed case=%s aid=%s err=%s",
            case_id,
            athlete_id,
            exc,
        )
        return False
