"""Runtime settings with Postgres override of env-var defaults.

The UI can edit a whitelist of keys (API keys, LLM choices, etc.) and the new
value is persisted in `app_settings` table. All collectors/LLM clients read
values through `get_runtime()` which checks DB first, then env.

Important: the cached layer (a process-local dict) is invalidated every time
the API writes a new value.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings as _env_settings
from app.db import AppSetting, session_scope

# Whitelist of keys editable from the UI. Anything else is ignored.
EDITABLE_KEYS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OLLAMA_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "DEFAULT_LLM",
    "GITHUB_TOKEN",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
    "COMPANIES_HOUSE_KEY",
    "RAPIDAPI_KEY",
    "SHODAN_API_KEY",
    "URLSCAN_API_KEY",
    "LEAKIX_API_KEY",
    "HUNTER_API_KEY",
    "NUMVERIFY_API_KEY",
)

# Subset of keys that should NEVER be returned in full when listing (GET /settings).
SECRET_KEYS = {
    "GEMINI_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN", "REDDIT_CLIENT_SECRET", "COMPANIES_HOUSE_KEY", "RAPIDAPI_KEY",
    "SHODAN_API_KEY", "URLSCAN_API_KEY", "LEAKIX_API_KEY", "HUNTER_API_KEY",
    "NUMVERIFY_API_KEY",
}

_cache: dict[str, str] | None = None
_lock = asyncio.Lock()


def _env_default(key: str) -> str:
    s = _env_settings()
    return str(getattr(s, key.lower(), "") or "")


async def _load_db() -> dict[str, str]:
    async with session_scope() as s:
        rows = (await s.execute(select(AppSetting))).scalars().all()
    return {r.key: r.value for r in rows}


async def get_runtime() -> dict[str, str]:
    """Return merged settings: env defaults + DB overrides. Cached in memory."""
    global _cache
    if _cache is not None:
        return _cache
    async with _lock:
        if _cache is not None:
            return _cache
        db = await _load_db()
        out: dict[str, str] = {}
        for k in EDITABLE_KEYS:
            out[k] = db.get(k) or _env_default(k)
        _cache = out
        return out


async def invalidate() -> None:
    global _cache
    _cache = None


async def put_many(values: dict[str, str], actor_ip: str | None = None) -> dict[str, str]:
    """Persist a batch of keys (only those in whitelist)."""
    clean = {k: (v or "") for k, v in values.items() if k in EDITABLE_KEYS}
    if not clean:
        return await get_runtime()
    async with session_scope() as s:
        for k, v in clean.items():
            stmt = pg_insert(AppSetting).values(key=k, value=v).on_conflict_do_update(
                index_elements=["key"], set_={"value": v}
            )
            await s.execute(stmt)
    await invalidate()
    return await get_runtime()


def redact(key: str, value: str) -> str:
    if not value or key not in SECRET_KEYS:
        return value
    if len(value) <= 8:
        return "••••"
    return value[:4] + "…" + value[-4:]


async def get_value(key: str) -> str:
    runtime = await get_runtime()
    return runtime.get(key) or ""


# Synchronous helper used by collectors during the event loop.
async def sget(key: str) -> str:
    return await get_value(key)
