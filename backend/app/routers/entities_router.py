"""Entity resolution REST endpoints.

# WIRING:
# In app/main.py, add (NOT applied here per task constraints):
#     from app.routers import entities_router
#     app.include_router(entities_router.router)
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db import session_scope

router = APIRouter(prefix="/api", tags=["entities"])


@router.get("/cases/{case_id}/entities")
async def list_entities(
    case_id: uuid.UUID,
    type: str | None = Query(default=None, description="Filter by entity type"),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
) -> list[dict]:
    """List resolved entities for a case, optionally filtered by type/score."""
    sql = (
        "SELECT id, type, value, attrs, score "
        "FROM entities WHERE case_id = :cid AND score >= :ms"
    )
    params: dict[str, Any] = {"cid": str(case_id), "ms": min_score}
    if type:
        sql += " AND type = :t"
        params["t"] = type
    sql += " ORDER BY score DESC, type, value"

    async with session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/cases/{case_id}/entities/graph")
async def entities_graph(case_id: uuid.UUID) -> dict:
    """Cytoscape-compatible graph of resolved entities for a case.

    Shape:
        {
          "nodes": [{"data": {"id": "<uuid>", "label": "...",
                              "type": "Email", "score": 0.97}}, ...],
          "edges": [{"data": {"id": "<a>__<b>", "source": "<a>",
                              "target": "<b>", "weight": 1, "via": "<finding_id>"}},
                    ...]
        }

    Edges are drawn between any two entities that share at least one
    underlying finding (co-occurrence edge).
    """
    async with session_scope() as s:
        ents = (await s.execute(
            text("SELECT id, type, value, score FROM entities WHERE case_id = :cid"),
            {"cid": str(case_id)},
        )).mappings().all()
        if not ents:
            raise HTTPException(404, "no entities resolved for this case")
        ent_ids = [str(r["id"]) for r in ents]

        # All (entity_id, finding_id) attributions for this case.
        srcs = (await s.execute(
            text(
                "SELECT entity_id, finding_id "
                "FROM entity_sources "
                "WHERE entity_id = ANY(:ids) AND finding_id IS NOT NULL"
            ),
            {"ids": ent_ids},
        )).mappings().all()

    # Index: finding_id → set(entity_id)
    by_finding: dict[str, set[str]] = {}
    for row in srcs:
        by_finding.setdefault(str(row["finding_id"]), set()).add(str(row["entity_id"]))

    # Build co-occurrence edges (deduped, undirected, with weight).
    edge_weights: dict[tuple[str, str], int] = {}
    edge_via: dict[tuple[str, str], str] = {}
    for fid, ent_set in by_finding.items():
        if len(ent_set) < 2:
            continue
        ent_list = sorted(ent_set)
        for i in range(len(ent_list)):
            for j in range(i + 1, len(ent_list)):
                key = (ent_list[i], ent_list[j])
                edge_weights[key] = edge_weights.get(key, 0) + 1
                edge_via.setdefault(key, fid)

    nodes = [
        {"data": {
            "id": str(r["id"]),
            "label": f"{r['type']}: {r['value']}",
            "type": r["type"],
            "value": r["value"],
            "score": float(r["score"]),
        }}
        for r in ents
    ]
    edges = [
        {"data": {
            "id": f"{a}__{b}",
            "source": a,
            "target": b,
            "weight": w,
            "via": edge_via[(a, b)],
        }}
        for (a, b), w in edge_weights.items()
    ]
    return {"nodes": nodes, "edges": edges}
