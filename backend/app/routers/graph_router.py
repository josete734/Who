"""Identity graph query endpoints (Wave 2/B1).

Read-only views over graph_nodes / graph_edges. Mutations are owned by
the entity-resolution pipeline (Agent A6) and not exposed here.

# WIRING: register in app/main.py with `app.include_router(graph_router.router)`
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import check_auth
from app.db import SessionLocal
from app.graph import projection, store

router = APIRouter(prefix="/api/cases", tags=["graph"])


async def _conn():
    async with SessionLocal() as session:
        yield session


@router.get("/{case_id}/graph", dependencies=[Depends(check_auth)])
async def get_graph(
    case_id: uuid.UUID,
    min_score: float | None = Query(default=None, ge=0.0, le=1.0),
    conn=Depends(_conn),
) -> dict:
    nodes, edges = await store.subgraph(conn, case_id=case_id, min_score=min_score)
    return projection.to_cytoscape(nodes, edges, min_score=min_score)


@router.get("/{case_id}/graph/path", dependencies=[Depends(check_auth)])
async def get_path(
    case_id: uuid.UUID,
    src: uuid.UUID = Query(...),
    dst: uuid.UUID = Query(...),
    max_depth: int = Query(default=6, ge=1, le=12),
    conn=Depends(_conn),
) -> dict:
    path = await store.shortest_path(
        conn, case_id=case_id, src=src, dst=dst, max_depth=max_depth
    )
    if not path:
        raise HTTPException(404, "no path found within max_depth")
    return {"path": [str(n) for n in path], "hops": len(path) - 1}


@router.get("/{case_id}/graph/neighbors/{node_id}", dependencies=[Depends(check_auth)])
async def get_neighbors(
    case_id: uuid.UUID,
    node_id: uuid.UUID,
    depth: int = Query(default=2, ge=0, le=6),
    conn=Depends(_conn),
) -> dict:
    nodes = await store.neighbors(conn, case_id=case_id, node_id=node_id, depth=depth)
    # Edges within the neighborhood: filter the case subgraph to these node ids.
    _all_nodes, all_edges = await store.subgraph(conn, case_id=case_id)
    keep = {n.id for n in nodes}
    edges = [e for e in all_edges if e.src in keep and e.dst in keep]
    return projection.to_cytoscape(nodes, edges)
