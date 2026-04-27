"""Curated collector-quality table.

Each collector has an intrinsic reliability weight in [0, 1]. These weights
are used by the scoring engine both as a *driver weight* (a high-quality
collector contributes more) and to flag *low-quality* collectors as a
penalty when no other corroboration exists.

Defaults are baked in (see ``DEFAULT_QUALITY``) but admins can override any
value at runtime via the `collector_quality` table (see migration
``NNNN_collector_quality.sql``). The router exposes GET / PATCH on
`/api/scoring/quality` for tuning.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import text

from app.db import session_scope

# Curated baseline. Values picked from operator experience and public
# accuracy reports; these are *not* probabilities, just relative quality.
DEFAULT_QUALITY: dict[str, float] = {
    "sherlock": 0.80,
    "maigret": 0.60,
    "github": 0.95,
    "holehe": 0.85,
    "hibp": 0.95,
    "hunter": 0.80,
    "dehashed": 0.90,
    "shodan": 0.90,
    "urlscan": 0.85,
    "leakix": 0.80,
    "companies_house": 0.95,
    "numverify": 0.75,
    "reddit": 0.70,
    "searxng": 0.55,
    "google_dork": 0.55,
    "wayback": 0.65,
    "manual": 1.00,
}

# Anything at or below this is considered "low quality" for penalty purposes.
LOW_QUALITY_THRESHOLD = 0.60


async def get_quality_table() -> dict[str, float]:
    """Return defaults merged with DB overrides."""
    out = dict(DEFAULT_QUALITY)
    try:
        async with session_scope() as s:
            rows = (
                await s.execute(text("SELECT name, weight FROM collector_quality"))
            ).all()
        for name, weight in rows:
            out[name] = float(weight)
    except Exception:
        # Table may not exist yet (e.g. tests without the migration).
        pass
    return out


async def set_collector_weight(name: str, weight: float) -> dict[str, float]:
    """Persist a single collector weight override and return the merged table."""
    if not name:
        raise ValueError("collector name required")
    w = max(0.0, min(1.0, float(weight)))
    async with session_scope() as s:
        await s.execute(
            text(
                """
                INSERT INTO collector_quality (name, weight, updated_at)
                VALUES (:n, :w, now())
                ON CONFLICT (name) DO UPDATE
                   SET weight = EXCLUDED.weight,
                       updated_at = now()
                """
            ),
            {"n": name, "w": w},
        )
    return await get_quality_table()


async def set_many(values: dict[str, float]) -> dict[str, float]:
    """Bulk update."""
    out: dict[str, float] = {}
    for k, v in values.items():
        out = await set_collector_weight(k, v)
    if not out:
        return await get_quality_table()
    return out


def quality_for(name: str, table: dict[str, float] | None = None) -> float:
    t = table if table is not None else DEFAULT_QUALITY
    return float(t.get(name, 0.5))  # unknown collectors default to neutral 0.5


def is_low_quality(name: str, table: dict[str, float] | None = None) -> bool:
    return quality_for(name, table) <= LOW_QUALITY_THRESHOLD


def collector_names(sources: Iterable) -> list[str]:
    return [getattr(s, "collector", "") or "" for s in sources]
