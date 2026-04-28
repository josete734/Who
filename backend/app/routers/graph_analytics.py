"""Graph analytics router (Wave 4).

Exposes ``GET /api/cases/{case_id}/graph/analytics`` which loads the
case subgraph, runs ``app.graph.analytics`` over it (centrality, Louvain
communities, brokers, hubs) and optionally persists the per-node scores
back into ``graph_nodes.attrs`` so subsequent ``GET /graph`` calls pick
them up without re-computing.

NetworkX is the only new dependency. We import it lazily so the module
can be imported (and skipped) in environments where the package isn't
available.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.auth import check_auth
from app.db import SessionLocal
from app.graph import store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["graph_analytics"])


_VALID_METRICS = {"centrality", "communities", "brokers", "hubs"}


async def _conn():
    async with SessionLocal() as session:
        yield session


@router.get(
    "/{case_id}/graph/analytics",
    dependencies=[Depends(check_auth)],
)
async def graph_analytics(
    case_id: uuid.UUID,
    metrics: str | None = Query(
        default=None,
        description=(
            "Comma-separated subset of: centrality, communities, brokers, "
            "hubs. Default: all."
        ),
    ),
    persist: bool = Query(
        default=False,
        description=(
            "If true, write degree/betweenness/community_id back into "
            "graph_nodes.attrs so the regular /graph endpoint surfaces them."
        ),
    ),
    min_score: float | None = Query(default=None, ge=0.0, le=1.0),
    conn=Depends(_conn),
) -> dict[str, Any]:
    # NetworkX is optional at the project level; bail with a 503 (not 500)
    # so the caller learns this is a configuration issue, not a bug.
    try:
        from app.graph import analytics
    except ImportError as exc:  # pragma: no cover — covered by unit test below
        raise HTTPException(503, f"networkx_not_installed: {exc}") from exc

    if metrics:
        requested = {m.strip() for m in metrics.split(",") if m.strip()}
        unknown = requested - _VALID_METRICS
        if unknown:
            raise HTTPException(
                400, f"unknown metrics: {sorted(unknown)} valid: {sorted(_VALID_METRICS)}"
            )
    else:
        requested = set(_VALID_METRICS)

    nodes, edges = await store.subgraph(conn, case_id=case_id, min_score=min_score)
    if not nodes:
        return {
            "case_id": str(case_id),
            "node_count": 0,
            "edge_count": 0,
            "result": {},
        }

    g = analytics.build_nx_graph(nodes, edges)

    result: dict[str, Any] = {
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
    }
    centrality_map: dict[str, dict[str, float]] = {}
    community_map: dict[str, int] = {}

    if "centrality" in requested:
        centrality_map = analytics.compute_centrality(g)
        result["centrality"] = centrality_map
    if "communities" in requested:
        community_map = analytics.detect_communities(g)
        result["communities"] = community_map
        result["community_count"] = (
            len(set(community_map.values())) if community_map else 0
        )
    if "brokers" in requested:
        # Brokers need centrality scores. Compute on demand if the caller
        # didn't ask for the full centrality output.
        cm = centrality_map or analytics.compute_centrality(g)
        result["brokers"] = analytics.find_brokers(cm)
    if "hubs" in requested:
        cm = centrality_map or analytics.compute_centrality(g)
        result["hubs"] = analytics.find_hubs(cm)

    if persist:
        try:
            await _persist_attrs(conn, case_id, centrality_map, community_map)
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("graph_analytics.persist_failed case=%s err=%s", case_id, exc)
            # Don't fail the response — the in-memory result is already valid.
            result["persist_error"] = str(exc)[:200]

    return {"case_id": str(case_id), **result}


async def _persist_attrs(
    conn: Any,
    case_id: uuid.UUID,
    centrality: dict[str, dict[str, float]],
    communities: dict[str, int],
) -> None:
    """Merge centrality + community_id into ``graph_nodes.attrs`` (JSONB).

    No-op if both inputs are empty. Uses a single JSONB merge per node so
    we don't clobber attrs written by other phases (e.g. entity resolution
    annotations).
    """
    if not centrality and not communities:
        return
    node_ids = set(centrality.keys()) | set(communities.keys())
    if not node_ids:
        return
    for nid in node_ids:
        attrs_patch: dict[str, Any] = {}
        if nid in centrality:
            attrs_patch["centrality"] = centrality[nid]
        if nid in communities:
            attrs_patch["community_id"] = communities[nid]
        if not attrs_patch:
            continue
        await conn.execute(
            text(
                "UPDATE graph_nodes SET attrs = attrs || CAST(:patch AS JSONB) "
                "WHERE id = :id AND case_id = :cid"
            ),
            {
                "patch": json.dumps(attrs_patch),
                "id": nid,
                "cid": str(case_id),
            },
        )
