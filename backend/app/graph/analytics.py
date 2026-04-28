"""Pure NetworkX analytics over the identity graph (Wave 4).

The functions here are deliberately I/O-free: they take an already-built
``networkx.Graph`` and return plain dicts/lists. Loading nodes/edges from
Postgres and writing scores back to ``graph_nodes.attrs`` is the router's
job (``app.routers.graph_analytics``).

This separation keeps the algorithms cheap to test with synthetic graphs
and lets the router stay thin.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import networkx as nx

log = logging.getLogger(__name__)


__all__ = [
    "build_nx_graph",
    "compute_centrality",
    "detect_communities",
    "find_brokers",
    "find_hubs",
    "summary",
]


# ---------------------------------------------------------------------------
# Graph construction from raw nodes/edges
# ---------------------------------------------------------------------------
def build_nx_graph(
    nodes: Iterable[Any],
    edges: Iterable[Any],
) -> nx.Graph:
    """Build an undirected, weighted ``networkx.Graph``.

    ``nodes``/``edges`` accept anything with the GraphNode / GraphEdge
    attribute shape (``id``, ``type``, ``key``, ``attrs``, ``score`` and
    ``src``, ``dst``, ``rel``, ``weight``). The ``id`` of each node becomes
    the NetworkX node id, kept as a string to avoid uuid serialisation
    headaches downstream.
    """
    g = nx.Graph()
    for n in nodes:
        nid = str(n.id)
        g.add_node(
            nid,
            type=n.type,
            key=n.key,
            score=float(n.score or 0.0),
            attrs=dict(n.attrs or {}),
        )
    for e in edges:
        src = str(e.src)
        dst = str(e.dst)
        if src == dst:
            continue
        if not g.has_node(src) or not g.has_node(dst):
            continue
        weight = float(e.weight or 1.0)
        # If we already saw an edge for this pair, keep the highest weight
        # — mirrors the upsert semantics of graph_edges.
        if g.has_edge(src, dst):
            existing = g[src][dst].get("weight", 0.0)
            if weight > existing:
                g[src][dst]["weight"] = weight
        else:
            g.add_edge(src, dst, weight=weight, rel=e.rel)
    return g


# ---------------------------------------------------------------------------
# Centrality
# ---------------------------------------------------------------------------
def compute_centrality(g: nx.Graph) -> dict[str, dict[str, float]]:
    """Return per-node centrality scores.

    Output shape: ``{node_id: {"degree": float, "betweenness": float,
    "eigenvector": float, "pagerank": float}}``.

    All four metrics are normalised in [0, 1] so they can be compared
    directly on the same axis (degree uses ``nx.degree_centrality`` which
    already normalises by N-1).
    """
    if g.number_of_nodes() == 0:
        return {}

    # Degree centrality is cheap; always compute it.
    deg = nx.degree_centrality(g)

    # Betweenness over a tiny graph is also cheap. We cap k to keep it
    # tractable on bigger cases via an approximation sample.
    n = g.number_of_nodes()
    if n <= 200:
        bet = nx.betweenness_centrality(g, normalized=True, weight="weight")
    else:
        # Approximate betweenness via random sampling for large graphs.
        bet = nx.betweenness_centrality(
            g, k=min(n, 100), normalized=True, weight="weight", seed=42
        )

    # Eigenvector centrality may not converge on disconnected graphs;
    # fall back to zeros if so.
    try:
        eig = nx.eigenvector_centrality_numpy(g, weight="weight")
    except Exception:
        try:
            eig = nx.eigenvector_centrality(
                g, max_iter=300, tol=1e-4, weight="weight"
            )
        except Exception:
            eig = {nid: 0.0 for nid in g.nodes()}

    # PageRank handles disconnected components natively.
    try:
        pr = nx.pagerank(g, alpha=0.85, weight="weight")
    except Exception:
        pr = {nid: 1.0 / max(1, n) for nid in g.nodes()}

    return {
        nid: {
            "degree": round(float(deg.get(nid, 0.0)), 6),
            "betweenness": round(float(bet.get(nid, 0.0)), 6),
            "eigenvector": round(float(eig.get(nid, 0.0)), 6),
            "pagerank": round(float(pr.get(nid, 0.0)), 6),
        }
        for nid in g.nodes()
    }


# ---------------------------------------------------------------------------
# Community detection (Louvain)
# ---------------------------------------------------------------------------
def detect_communities(
    g: nx.Graph, *, resolution: float = 1.0, seed: int = 42
) -> dict[str, int]:
    """Map node id → community id (0-indexed) via Louvain.

    For a graph with no edges, every node is its own community. Returns an
    empty dict for an empty graph.
    """
    if g.number_of_nodes() == 0:
        return {}
    if g.number_of_edges() == 0:
        return {nid: i for i, nid in enumerate(g.nodes())}

    # Louvain requires a non-empty graph; on tiny ones it still works.
    try:
        communities = nx.community.louvain_communities(
            g, weight="weight", resolution=resolution, seed=seed
        )
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("louvain.failed err=%s falling back to connected components", exc)
        communities = list(nx.connected_components(g))

    out: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for nid in members:
            out[str(nid)] = cid
    return out


# ---------------------------------------------------------------------------
# Roles: brokers and hubs
# ---------------------------------------------------------------------------
def find_brokers(
    centrality: dict[str, dict[str, float]],
    *,
    min_betweenness: float = 0.05,
    max_degree: float = 0.5,
) -> list[str]:
    """Brokers connect otherwise-disjoint communities.

    Heuristic: high betweenness with relatively low degree. The defaults
    surface intermediaries on small-to-medium graphs; tune at the call site
    if needed.
    """
    if not centrality:
        return []
    # Sort by betweenness desc to keep results deterministic.
    candidates = sorted(
        centrality.items(),
        key=lambda kv: kv[1].get("betweenness", 0.0),
        reverse=True,
    )
    out: list[str] = []
    for nid, scores in candidates:
        if scores.get("betweenness", 0.0) < min_betweenness:
            continue
        if scores.get("degree", 0.0) > max_degree:
            continue
        out.append(nid)
    return out


def find_hubs(
    centrality: dict[str, dict[str, float]],
    *,
    top_pct: float = 0.05,
    min_count: int = 1,
) -> list[str]:
    """Hubs are the highest-degree nodes (top ``top_pct`` of all nodes)."""
    if not centrality:
        return []
    items = sorted(
        centrality.items(),
        key=lambda kv: kv[1].get("degree", 0.0),
        reverse=True,
    )
    n = len(items)
    take = max(min_count, int(round(n * max(0.0, min(top_pct, 1.0)))))
    return [nid for nid, _scores in items[:take]]


# ---------------------------------------------------------------------------
# Convenience aggregator (used by the router for a one-shot call)
# ---------------------------------------------------------------------------
def summary(g: nx.Graph) -> dict[str, Any]:
    """Compute everything in one go and return a serialisable dict."""
    centrality = compute_centrality(g)
    communities = detect_communities(g)
    brokers = find_brokers(centrality)
    hubs = find_hubs(centrality)
    n_communities = len(set(communities.values())) if communities else 0
    return {
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "community_count": n_communities,
        "centrality": centrality,
        "communities": communities,
        "brokers": brokers,
        "hubs": hubs,
    }
