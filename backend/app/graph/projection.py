"""Project graph store rows to Cytoscape.js JSON.

Cytoscape expects:
    {
      "nodes": [{"data": {"id": ..., "label": ..., "type": ..., "score": ...}}],
      "edges": [{"data": {"source": ..., "target": ..., "rel": ..., "weight": ...}}]
    }
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from app.graph.store import GraphEdge, GraphNode


def to_cytoscape(
    nodes: "Iterable[GraphNode]",
    edges: "Iterable[GraphEdge]",
    *,
    min_score: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build a Cytoscape-compatible payload.

    `min_score` filters nodes (and any edges referencing dropped nodes).
    """
    keep_nodes: list[GraphNode] = []
    for n in nodes:
        if min_score is not None and n.score < min_score:
            continue
        keep_nodes.append(n)

    keep_ids = {n.id for n in keep_nodes}

    cy_nodes = [
        {
            "data": {
                "id": str(n.id),
                "label": n.attrs.get("label") or n.key,
                "type": n.type,
                "score": round(float(n.score), 4),
                **({"attrs": n.attrs} if n.attrs else {}),
            }
        }
        for n in keep_nodes
    ]

    cy_edges = [
        {
            "data": {
                "id": str(e.id),
                "source": str(e.src),
                "target": str(e.dst),
                "rel": e.rel,
                "weight": round(float(e.weight), 4),
                **({"evidence": e.evidence} if e.evidence else {}),
            }
        }
        for e in edges
        if e.src in keep_ids and e.dst in keep_ids
    ]

    return {"nodes": cy_nodes, "edges": cy_edges}
