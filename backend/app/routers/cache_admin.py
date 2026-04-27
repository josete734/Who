# WIRING: include_router(cache_admin.router, prefix="/api/cache")
"""Admin endpoints for the collector cache layer.

NOTE: this router is NOT wired into ``app.main`` yet. Someone else will add
the include_router call shown above when integration is approved.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import cache

router = APIRouter(prefix="/api/cache", tags=["cache-admin"])


@router.get("/stats")
async def cache_stats() -> dict:
    """Return raw hit/miss counters tracked under ``cache:stats:*``."""
    stats = await cache.get_stats()
    hit = stats.get("hit", 0)
    miss = stats.get("miss", 0)
    total = hit + miss
    ratio = (hit / total) if total else 0.0
    return {
        "hit": hit,
        "miss": miss,
        "total": total,
        "hit_ratio": round(ratio, 4),
        "by_key": stats,
    }


@router.delete("/{prefix}")
async def flush_prefix(prefix: str) -> dict:
    """Delete every cache entry under ``cache:<prefix>:*`` using SCAN.

    ``prefix`` is typically the collector name (e.g. ``sherlock``,
    ``gemini_websearch``). To flush stats counters pass ``stats``.
    """
    if not prefix or "/" in prefix or "*" in prefix:
        raise HTTPException(status_code=400, detail="invalid prefix")
    deleted = await cache.scan_delete_prefix(prefix)
    return {"prefix": prefix, "deleted": deleted}
