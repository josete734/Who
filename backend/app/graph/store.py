"""Async store for identity graph nodes/edges.

All functions accept an `AsyncConnection` or `AsyncSession`-compatible
executor (anything exposing `.execute(text, params)` returning a result
with `.mappings().all()` / `.first()` semantics, i.e. SQLAlchemy 2.x).

The store is intentionally thin: callers (Agent A6) own transactional
batching. We use plain `text()` queries so the module has no ORM coupling
and is easy to drive from tests with an in-memory SQLite-compatible shim
or from the existing async PG engine.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Protocol

from sqlalchemy import text

from app.graph.types import GraphEdge, GraphNode

__all__ = [
    "GraphEdge",
    "GraphNode",
    "neighbors",
    "shortest_path",
    "subgraph",
    "upsert_edge",
    "upsert_node",
]


class _Executor(Protocol):
    async def execute(self, statement: Any, params: Any | None = ...) -> Any: ...


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------
_UPSERT_NODE = text(
    """
    INSERT INTO graph_nodes (id, case_id, type, key, attrs, score)
    VALUES (:id, :case_id, :type, :key, CAST(:attrs AS JSONB), :score)
    ON CONFLICT (case_id, type, key) DO UPDATE
      SET attrs = graph_nodes.attrs || EXCLUDED.attrs,
          score = GREATEST(graph_nodes.score, EXCLUDED.score)
    RETURNING id, case_id, type, key, attrs, score
    """
)

_UPSERT_EDGE = text(
    """
    INSERT INTO graph_edges (id, case_id, src, dst, rel, weight, evidence)
    VALUES (:id, :case_id, :src, :dst, :rel, :weight, CAST(:evidence AS JSONB))
    ON CONFLICT (case_id, src, dst, rel) DO UPDATE
      SET weight = GREATEST(graph_edges.weight, EXCLUDED.weight),
          evidence = graph_edges.evidence || EXCLUDED.evidence
    RETURNING id, case_id, src, dst, rel, weight, evidence
    """
)


async def upsert_node(
    conn: _Executor,
    *,
    case_id: uuid.UUID,
    type: str,
    key: str,
    attrs: dict[str, Any] | None = None,
    score: float = 0.0,
    node_id: uuid.UUID | None = None,
) -> GraphNode:
    """Idempotent upsert keyed on (case_id, type, key).

    Merges `attrs` JSONB and keeps the higher score on conflict.
    """
    payload = {
        "id": str(node_id or uuid.uuid4()),
        "case_id": str(case_id),
        "type": type,
        "key": key,
        "attrs": json.dumps(attrs or {}),
        "score": float(score),
    }
    res = await conn.execute(_UPSERT_NODE, payload)
    row = res.mappings().first()
    return GraphNode(
        id=uuid.UUID(str(row["id"])),
        case_id=uuid.UUID(str(row["case_id"])),
        type=row["type"],
        key=row["key"],
        attrs=row["attrs"] or {},
        score=float(row["score"] or 0.0),
    )


async def upsert_edge(
    conn: _Executor,
    *,
    case_id: uuid.UUID,
    src: uuid.UUID,
    dst: uuid.UUID,
    rel: str,
    weight: float = 1.0,
    evidence: dict[str, Any] | None = None,
    edge_id: uuid.UUID | None = None,
) -> GraphEdge:
    """Idempotent upsert keyed on (case_id, src, dst, rel)."""
    payload = {
        "id": str(edge_id or uuid.uuid4()),
        "case_id": str(case_id),
        "src": str(src),
        "dst": str(dst),
        "rel": rel,
        "weight": float(weight),
        "evidence": json.dumps(evidence or {}),
    }
    res = await conn.execute(_UPSERT_EDGE, payload)
    row = res.mappings().first()
    return GraphEdge(
        id=uuid.UUID(str(row["id"])),
        case_id=uuid.UUID(str(row["case_id"])),
        src=uuid.UUID(str(row["src"])),
        dst=uuid.UUID(str(row["dst"])),
        rel=row["rel"],
        weight=float(row["weight"] or 0.0),
        evidence=row["evidence"] or {},
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
_NEIGHBORS = text(
    """
    WITH RECURSIVE walk(node_id, depth) AS (
        SELECT :start::uuid, 0
        UNION ALL
        SELECT CASE WHEN e.src = w.node_id THEN e.dst ELSE e.src END,
               w.depth + 1
        FROM walk w
        JOIN graph_edges e
          ON e.case_id = :case_id
         AND (e.src = w.node_id OR e.dst = w.node_id)
        WHERE w.depth < :max_depth
    )
    SELECT DISTINCT n.id, n.case_id, n.type, n.key, n.attrs, n.score
      FROM walk w
      JOIN graph_nodes n ON n.id = w.node_id AND n.case_id = :case_id
    """
)


async def neighbors(
    conn: _Executor,
    *,
    case_id: uuid.UUID,
    node_id: uuid.UUID,
    depth: int = 1,
) -> list[GraphNode]:
    """Return nodes within `depth` hops of `node_id` (inclusive)."""
    if depth < 0:
        depth = 0
    res = await conn.execute(
        _NEIGHBORS,
        {"case_id": str(case_id), "start": str(node_id), "max_depth": int(depth)},
    )
    return [
        GraphNode(
            id=uuid.UUID(str(r["id"])),
            case_id=uuid.UUID(str(r["case_id"])),
            type=r["type"],
            key=r["key"],
            attrs=r["attrs"] or {},
            score=float(r["score"] or 0.0),
        )
        for r in res.mappings().all()
    ]


_SUBGRAPH_NODES = text(
    """
    SELECT id, case_id, type, key, attrs, score
      FROM graph_nodes
     WHERE case_id = :case_id
       AND (:min_score IS NULL OR score >= :min_score)
    """
)
_SUBGRAPH_EDGES = text(
    """
    SELECT e.id, e.case_id, e.src, e.dst, e.rel, e.weight, e.evidence
      FROM graph_edges e
     WHERE e.case_id = :case_id
       AND (:min_score IS NULL OR EXISTS (
              SELECT 1 FROM graph_nodes n
               WHERE n.id IN (e.src, e.dst) AND n.score >= :min_score
           ))
    """
)


async def subgraph(
    conn: _Executor,
    *,
    case_id: uuid.UUID,
    min_score: float | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Full per-case subgraph, optionally filtered by node score."""
    params = {"case_id": str(case_id), "min_score": min_score}
    n_res = await conn.execute(_SUBGRAPH_NODES, params)
    e_res = await conn.execute(_SUBGRAPH_EDGES, params)
    nodes = [
        GraphNode(
            id=uuid.UUID(str(r["id"])),
            case_id=uuid.UUID(str(r["case_id"])),
            type=r["type"],
            key=r["key"],
            attrs=r["attrs"] or {},
            score=float(r["score"] or 0.0),
        )
        for r in n_res.mappings().all()
    ]
    node_ids = {n.id for n in nodes}
    edges = []
    for r in e_res.mappings().all():
        src = uuid.UUID(str(r["src"]))
        dst = uuid.UUID(str(r["dst"]))
        # Drop dangling edges whose endpoints were filtered out.
        if src not in node_ids or dst not in node_ids:
            continue
        edges.append(
            GraphEdge(
                id=uuid.UUID(str(r["id"])),
                case_id=uuid.UUID(str(r["case_id"])),
                src=src,
                dst=dst,
                rel=r["rel"],
                weight=float(r["weight"] or 0.0),
                evidence=r["evidence"] or {},
            )
        )
    return nodes, edges


# ---------------------------------------------------------------------------
# Shortest path (recursive CTE; undirected, unweighted-min-hops)
# ---------------------------------------------------------------------------
_SHORTEST_PATH = text(
    """
    WITH RECURSIVE paths(node_id, path, depth) AS (
        SELECT :src::uuid,
               ARRAY[:src::uuid],
               0
        UNION ALL
        SELECT CASE WHEN e.src = p.node_id THEN e.dst ELSE e.src END AS next_id,
               p.path || (CASE WHEN e.src = p.node_id THEN e.dst ELSE e.src END),
               p.depth + 1
        FROM paths p
        JOIN graph_edges e
          ON e.case_id = :case_id
         AND (e.src = p.node_id OR e.dst = p.node_id)
        WHERE p.depth < :max_depth
          AND NOT (CASE WHEN e.src = p.node_id THEN e.dst ELSE e.src END = ANY(p.path))
    )
    SELECT path
      FROM paths
     WHERE node_id = :dst::uuid
     ORDER BY depth ASC
     LIMIT 1
    """
)


async def shortest_path(
    conn: _Executor,
    *,
    case_id: uuid.UUID,
    src: uuid.UUID,
    dst: uuid.UUID,
    max_depth: int = 6,
) -> list[uuid.UUID]:
    """Return the shortest undirected path of node IDs (inclusive of src/dst).

    Empty list if no path exists within `max_depth` hops.
    """
    if src == dst:
        return [src]
    res = await conn.execute(
        _SHORTEST_PATH,
        {
            "case_id": str(case_id),
            "src": str(src),
            "dst": str(dst),
            "max_depth": int(max_depth),
        },
    )
    row = res.first()
    if not row:
        return []
    raw = row[0]
    return [uuid.UUID(str(x)) for x in raw]
