"""Identity graph persistence + projection (Wave 2/B1).

Modules:
    store:      async upsert/query helpers backed by graph_nodes/graph_edges.
    projection: convert subgraph results into Cytoscape JSON.
"""
from __future__ import annotations

from app.graph.projection import to_cytoscape
from app.graph.types import GraphEdge, GraphNode


def __getattr__(name: str):  # pragma: no cover - lazy DB-bound imports
    """Lazy-load store helpers to avoid SQLAlchemy import for projection-only users."""
    if name in {"neighbors", "shortest_path", "subgraph", "upsert_edge", "upsert_node"}:
        from app.graph import store

        return getattr(store, name)
    raise AttributeError(name)

__all__ = [
    "GraphEdge",
    "GraphNode",
    "neighbors",
    "shortest_path",
    "subgraph",
    "to_cytoscape",
    "upsert_edge",
    "upsert_node",
]
