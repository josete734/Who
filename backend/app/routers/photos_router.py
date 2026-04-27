"""Photo aggregator + clustering HTTP API (Wave 2 / B3).

# WIRING ----------------------------------------------------------------
# Not auto-registered. To activate, add to main.py:
#
#       from app.routers.photos_router import router as photos_router
#       app.include_router(photos_router)
# -----------------------------------------------------------------------
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import session_scope
from app.photos.aggregator import collect_photos
from app.photos.clusterer import cluster_photos
from app.photos.gallery import build_gallery

router = APIRouter(prefix="/api/cases", tags=["photos"])


@router.post("/{case_id}/photos/collect")
async def collect_endpoint(case_id: uuid.UUID) -> dict:
    return await collect_photos(case_id)


@router.post("/{case_id}/photos/cluster")
async def cluster_endpoint(case_id: uuid.UUID) -> dict:
    return await cluster_photos(case_id)


@router.get("/{case_id}/photos")
async def list_photos(case_id: uuid.UUID) -> dict:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, url, sha256, phash, width, height, mime, "
                    "cluster_id, source_finding_id, downloaded_at "
                    "FROM photos WHERE case_id = :cid "
                    "ORDER BY downloaded_at DESC"
                ),
                {"cid": str(case_id)},
            )
        ).all()
    return {
        "case_id": str(case_id),
        "count": len(rows),
        "photos": [
            {
                "id": str(r.id),
                "url": r.url,
                "sha256": r.sha256,
                "phash": r.phash,
                "width": r.width,
                "height": r.height,
                "mime": r.mime,
                "cluster_id": str(r.cluster_id) if r.cluster_id else None,
                "source_finding_id": str(r.source_finding_id) if r.source_finding_id else None,
                "downloaded_at": r.downloaded_at.isoformat() if r.downloaded_at else None,
            }
            for r in rows
        ],
    }


@router.get("/{case_id}/photos/clusters")
async def list_clusters(case_id: uuid.UUID) -> dict:
    try:
        return await build_gallery(case_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
