"""Gallery view (Wave 2 / B3).

Reads ``photos`` + ``photo_clusters`` for a case and returns a JSON-friendly
structure: clusters with a representative thumbnail, member list, and the
collectors that originally produced each photo (joined via findings).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.db import session_scope


async def build_gallery(case_id: uuid.UUID | str) -> dict[str, Any]:
    if isinstance(case_id, str):
        case_id = uuid.UUID(case_id)

    async with session_scope() as s:
        photos = (
            await s.execute(
                text(
                    """
                    SELECT p.id, p.url, p.phash, p.width, p.height, p.mime,
                           p.cluster_id, p.source_finding_id, f.collector
                      FROM photos p
                      LEFT JOIN findings f ON f.id = p.source_finding_id
                     WHERE p.case_id = :cid
                    """
                ),
                {"cid": str(case_id)},
            )
        ).all()
        clusters = (
            await s.execute(
                text(
                    "SELECT id, label, score, count, representative_photo_id "
                    "FROM photo_clusters WHERE case_id = :cid"
                ),
                {"cid": str(case_id)},
            )
        ).all()

    photo_by_id = {p.id: p for p in photos}
    cluster_members: dict[uuid.UUID, list[Any]] = {}
    for p in photos:
        if p.cluster_id is not None:
            cluster_members.setdefault(p.cluster_id, []).append(p)

    out_clusters: list[dict[str, Any]] = []
    for c in clusters:
        members = cluster_members.get(c.id, [])
        rep = photo_by_id.get(c.representative_photo_id)
        collectors = sorted({m.collector for m in members if m.collector})
        out_clusters.append(
            {
                "id": str(c.id),
                "label": c.label,
                "score": c.score,
                "count": c.count,
                "representative": {
                    "id": str(rep.id) if rep else None,
                    "url": rep.url if rep else None,
                    "mime": rep.mime if rep else None,
                },
                "members": [
                    {
                        "id": str(m.id),
                        "url": m.url,
                        "mime": m.mime,
                        "width": m.width,
                        "height": m.height,
                        "phash": m.phash,
                        "source_finding_id": str(m.source_finding_id) if m.source_finding_id else None,
                        "collector": m.collector,
                    }
                    for m in members
                ],
                "collectors": collectors,
            }
        )

    # Photos that didn't make it into any cluster.
    unclustered = [
        {
            "id": str(p.id),
            "url": p.url,
            "mime": p.mime,
            "width": p.width,
            "height": p.height,
            "phash": p.phash,
            "collector": p.collector,
        }
        for p in photos
        if p.cluster_id is None
    ]

    return {
        "case_id": str(case_id),
        "total_photos": len(photos),
        "clusters": out_clusters,
        "unclustered": unclustered,
    }
