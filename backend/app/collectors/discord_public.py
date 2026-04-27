"""Discord public profile lookup (no auth, public/community endpoints only).

Discord's official `users/{id}` endpoint requires a Bot/Bearer token, so we
deliberately skip it. Instead we delegate to a community lookup mirror, whose
base URL is configurable via the ``DISCORD_LOOKUP_BASE`` env var.

Inputs:
  - ``username``: Discord handle (without leading ``@``).
  - ``discord_id``: numeric snowflake (taken from ``SearchInput.extra_context``
    when provided as ``discord_id=...``).

Findings expose: discord_id, username, avatar_url, banner, badges[],
created_at_estimate (decoded from the snowflake).

This collector is intentionally NOT registered in the orchestrator yet — see
the WIRING comment at the bottom of the file.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


# Discord epoch: 2015-01-01T00:00:00Z (ms).
DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake_to_datetime(snowflake: int | str) -> dt.datetime:
    """Decode a Discord snowflake into its creation timestamp (UTC).

    The upper 42 bits of a Discord snowflake encode the milliseconds elapsed
    since the Discord epoch (2015-01-01T00:00:00Z).
    """
    sid = int(snowflake)
    ms_since_epoch = (sid >> 22) + DISCORD_EPOCH_MS
    return dt.datetime.fromtimestamp(ms_since_epoch / 1000.0, tz=dt.timezone.utc)


def _extract_discord_id(input: SearchInput) -> str | None:
    """Pull a numeric Discord ID out of ``extra_context`` if present."""
    ctx = input.extra_context or ""
    m = re.search(r"discord_id\s*[=:]\s*(\d{15,25})", ctx)
    if m:
        return m.group(1)
    # Bare 17-20 digit token in extra_context (snowflake range).
    m = re.search(r"\b(\d{17,20})\b", ctx)
    return m.group(1) if m else None


def _lookup_base() -> str:
    return os.environ.get("DISCORD_LOOKUP_BASE", "https://discordlookup.com/api/v1").rstrip("/")


def _badges_from(payload: dict[str, Any]) -> list[str]:
    """Normalize badges across known community-lookup shapes."""
    raw = payload.get("badges") or payload.get("public_flags_array") or []
    if isinstance(raw, list):
        return [str(b) for b in raw]
    if isinstance(raw, dict):
        return [k for k, v in raw.items() if v]
    return []


@register
class DiscordPublicCollector(Collector):
    name = "discord_public"
    category = "social"
    needs = ("username", "extra_context")
    timeout_seconds = 15
    description = "Discord public lookup via configurable community mirror (no auth)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        discord_id = _extract_discord_id(input)
        username = (input.username or "").lstrip("@") or None

        if discord_id:
            async for f in self._lookup_by_id(discord_id):
                yield f
            return

        if username:
            # No reliable unauthenticated username->profile index is publicly
            # available. We deliberately return nothing rather than emit a
            # speculative finding.
            return
        return

    async def _lookup_by_id(self, discord_id: str) -> AsyncIterator[Finding]:
        url = f"{_lookup_base()}/user/{discord_id}"
        async with client(timeout=10) as c:
            try:
                r = await c.get(url, headers={"Accept": "application/json"})
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return
        if not isinstance(data, dict) or not data:
            return

        # The community APIs vary; accept either a flat object or {"user": {...}}.
        body = data.get("user") if isinstance(data.get("user"), dict) else data
        sid = str(body.get("id") or discord_id)

        try:
            created = snowflake_to_datetime(sid).isoformat()
        except (TypeError, ValueError):
            created = None

        username = body.get("username") or body.get("global_name") or body.get("tag")
        avatar_url = body.get("avatar_url") or body.get("avatarURL") or body.get("avatar")
        banner = body.get("banner_url") or body.get("bannerURL") or body.get("banner")
        badges = _badges_from(body)

        yield Finding(
            collector=self.name,
            category="username",
            entity_type="DiscordProfile",
            title=f"Discord: {username or sid}",
            url=f"https://discord.com/users/{sid}",
            confidence=0.7,
            payload={
                "discord_id": sid,
                "username": username,
                "avatar_url": avatar_url,
                "banner": banner,
                "badges": badges,
                "created_at_estimate": created,
                "source": _lookup_base(),
                "note": (
                    "Datos via mirror comunitario (DISCORD_LOOKUP_BASE). "
                    "Discord oficial requiere auth; verifica antes de citar."
                ),
            },
        )


# ---------------------------------------------------------------------------
# WIRING — DO NOT register in the orchestrator yet (Wave 3/C4).
# To activate: add ``@register`` above the class definition, ensure
# ``SearchInput`` exposes a way to pass a Discord snowflake (currently parsed
# out of ``extra_context``), and configure ``DISCORD_LOOKUP_BASE`` to a
# trusted public mirror (e.g. ``https://discordlookup.com/api/v1`` or
# ``https://discord-lookup.app/api/lookup`` — note the latter has no
# ``/user/`` segment, adapt ``_lookup_by_id`` accordingly).
# ---------------------------------------------------------------------------
