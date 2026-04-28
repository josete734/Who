"""Tests for Wave 4 — graph analytics (NetworkX-based).

The tests build small synthetic graphs in memory and assert that the
analytics functions produce sensible Palantir-lite signal:

* ``compute_centrality`` returns four normalised scores per node.
* ``detect_communities`` recovers the planted three-community structure.
* ``find_brokers`` highlights the bridge node connecting communities.
* ``find_hubs`` returns the densest centres.
* ``build_nx_graph`` keeps the highest weight on duplicate edges.
* ``summary`` aggregates everything in one call.
"""
from __future__ import annotations

import pytest

# Skip the whole module gracefully if NetworkX is missing (e.g. CI without
# the prod deps). Import after the skip so test collection still succeeds.
nx = pytest.importorskip("networkx")

from app.graph.analytics import (
    build_nx_graph,
    compute_centrality,
    detect_communities,
    find_brokers,
    find_hubs,
    summary,
)


# ---------------------------------------------------------------------------
# Lightweight stand-ins for GraphNode / GraphEdge so we don't need uuid /
# the full SQLAlchemy stack to drive the algorithms.
# ---------------------------------------------------------------------------
class _N:
    __slots__ = ("id", "type", "key", "attrs", "score")

    def __init__(self, id, type="Person", key="", attrs=None, score=0.0):
        self.id = id
        self.type = type
        self.key = key or str(id)
        self.attrs = attrs or {}
        self.score = score


class _E:
    __slots__ = ("id", "src", "dst", "rel", "weight")

    def __init__(self, src, dst, rel="link", weight=1.0):
        self.id = f"{src}-{dst}"
        self.src = src
        self.dst = dst
        self.rel = rel
        self.weight = weight


# Three densely-connected triangles, each linked to the next by exactly one
# bridge node:
#
#   A1—A2—A3—X—B1—B2—B3—Y—C1—C2—C3
#       \_/      \_/      \_/
#
# X and Y are textbook brokers: they sit on every shortest path between
# their two neighbouring communities.
def _three_communities():
    nodes = [
        _N("A1"), _N("A2"), _N("A3"),
        _N("B1"), _N("B2"), _N("B3"),
        _N("C1"), _N("C2"), _N("C3"),
        _N("X"), _N("Y"),
    ]
    edges = [
        # Community A
        _E("A1", "A2"), _E("A2", "A3"), _E("A1", "A3"),
        # Community B
        _E("B1", "B2"), _E("B2", "B3"), _E("B1", "B3"),
        # Community C
        _E("C1", "C2"), _E("C2", "C3"), _E("C1", "C3"),
        # Bridges
        _E("A3", "X"), _E("X", "B1"),
        _E("B3", "Y"), _E("Y", "C1"),
    ]
    return nodes, edges


# ---------------------------------------------------------------------------
# build_nx_graph
# ---------------------------------------------------------------------------
def test_build_nx_graph_basic_shape():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    assert g.number_of_nodes() == 11
    assert g.number_of_edges() == 13
    assert g.has_edge("A1", "A2")
    assert g.has_edge("X", "B1")


def test_build_nx_graph_keeps_max_weight_on_duplicates():
    nodes = [_N("a"), _N("b")]
    edges = [_E("a", "b", weight=0.3), _E("a", "b", weight=0.9), _E("a", "b", weight=0.5)]
    g = build_nx_graph(nodes, edges)
    assert g.number_of_edges() == 1
    assert g["a"]["b"]["weight"] == 0.9


def test_build_nx_graph_drops_self_loops():
    nodes = [_N("a"), _N("b")]
    edges = [_E("a", "a"), _E("a", "b")]
    g = build_nx_graph(nodes, edges)
    assert g.number_of_edges() == 1
    assert not g.has_edge("a", "a")


def test_build_nx_graph_drops_dangling_edges():
    nodes = [_N("a")]
    edges = [_E("a", "missing"), _E("missing", "a")]
    g = build_nx_graph(nodes, edges)
    assert g.number_of_edges() == 0


# ---------------------------------------------------------------------------
# compute_centrality
# ---------------------------------------------------------------------------
def test_compute_centrality_returns_four_metrics_per_node():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    c = compute_centrality(g)
    assert set(c.keys()) == {n.id for n in nodes}
    for nid, scores in c.items():
        assert set(scores.keys()) == {"degree", "betweenness", "eigenvector", "pagerank"}
        for v in scores.values():
            assert 0.0 <= v <= 1.0, f"out-of-range score {nid} {v}"


def test_compute_centrality_brokers_have_high_betweenness():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    c = compute_centrality(g)
    # Brokers X and Y should have higher betweenness than triangle members.
    bet_x = c["X"]["betweenness"]
    bet_y = c["Y"]["betweenness"]
    bet_a1 = c["A1"]["betweenness"]
    assert bet_x > bet_a1
    assert bet_y > bet_a1


def test_compute_centrality_empty_graph_returns_empty():
    g = nx.Graph()
    assert compute_centrality(g) == {}


# ---------------------------------------------------------------------------
# detect_communities
# ---------------------------------------------------------------------------
def test_detect_communities_recovers_three_groups():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    communities = detect_communities(g)
    # Every node assigned exactly one community.
    assert set(communities.keys()) == {n.id for n in nodes}
    # Three planted communities ⇒ at least three distinct community ids.
    n_communities = len(set(communities.values()))
    assert n_communities >= 3, f"expected ≥3 communities, got {n_communities}"
    # Triangle members co-cluster.
    assert communities["A1"] == communities["A2"] == communities["A3"]
    assert communities["B1"] == communities["B2"] == communities["B3"]
    assert communities["C1"] == communities["C2"] == communities["C3"]
    # The three triangles are NOT in the same community as each other.
    assert communities["A1"] != communities["B1"]
    assert communities["B1"] != communities["C1"]


def test_detect_communities_no_edges_each_node_alone():
    nodes = [_N("a"), _N("b"), _N("c")]
    g = build_nx_graph(nodes, [])
    communities = detect_communities(g)
    assert len(set(communities.values())) == 3


def test_detect_communities_empty_graph():
    g = nx.Graph()
    assert detect_communities(g) == {}


# ---------------------------------------------------------------------------
# find_brokers / find_hubs
# ---------------------------------------------------------------------------
def test_find_brokers_returns_bridge_nodes():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    c = compute_centrality(g)
    brokers = find_brokers(c, min_betweenness=0.05, max_degree=0.5)
    # X and Y must be in the broker set (they are the only bridges).
    assert "X" in brokers, brokers
    assert "Y" in brokers, brokers
    # Triangle leaves with low betweenness must NOT be in the broker set.
    assert "A1" not in brokers
    assert "B2" not in brokers


def test_find_hubs_returns_top_pct():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    c = compute_centrality(g)
    hubs = find_hubs(c, top_pct=0.20, min_count=2)
    # 20% of 11 nodes ≈ 2 nodes. They should be the highest-degree ones.
    assert len(hubs) >= 2
    # Brokers and triangle nodes both have degree 3 here — any with degree
    # >= 3 is acceptable. Just ensure the hub is one of those.
    sorted_by_degree = sorted(c.items(), key=lambda kv: kv[1]["degree"], reverse=True)
    top_id = sorted_by_degree[0][0]
    assert top_id in hubs


def test_find_brokers_empty_input():
    assert find_brokers({}) == []


def test_find_hubs_empty_input():
    assert find_hubs({}) == []


# ---------------------------------------------------------------------------
# summary aggregator
# ---------------------------------------------------------------------------
def test_summary_returns_full_report():
    nodes, edges = _three_communities()
    g = build_nx_graph(nodes, edges)
    s = summary(g)
    assert s["node_count"] == 11
    assert s["edge_count"] == 13
    assert s["community_count"] >= 3
    assert "centrality" in s and len(s["centrality"]) == 11
    assert "communities" in s
    assert "brokers" in s and len(s["brokers"]) >= 2
    assert "hubs" in s and len(s["hubs"]) >= 1
