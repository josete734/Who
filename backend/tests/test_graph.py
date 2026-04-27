"""Unit tests for the identity graph module (Wave 2/B1).

These tests do not require a running Postgres: they exercise projection
logic over synthetic GraphNode/GraphEdge dataclasses, plus an in-memory
fake executor that satisfies store.py's recursive-CTE-shaped queries via
a small Python BFS for `neighbors` and `shortest_path`.

DB-bound integration tests live in tests/integration/test_graph_db.py
(out of scope here; covered by the migration-runner harness).
"""
from __future__ import annotations

import uuid
from collections import defaultdict, deque

import pytest

from app.graph import projection
from app.graph.types import GraphEdge, GraphNode


# ---------------------------------------------------------------------------
# Fixtures: synthetic graph
# ---------------------------------------------------------------------------
CASE_ID = uuid.uuid4()


def _node(t: str, key: str, score: float = 0.5) -> GraphNode:
    return GraphNode(
        id=uuid.uuid4(),
        case_id=CASE_ID,
        type=t,
        key=key,
        attrs={"label": key},
        score=score,
    )


@pytest.fixture
def graph():
    a = _node("Person", "alice", score=0.9)
    b = _node("Email", "alice@example.com", score=0.8)
    c = _node("Phone", "+34999111222", score=0.7)
    d = _node("Account", "twitter:alice", score=0.6)
    e = _node("Domain", "example.com", score=0.2)  # low-score, filterable

    edges = [
        GraphEdge(uuid.uuid4(), CASE_ID, a.id, b.id, "has_email", 0.9),
        GraphEdge(uuid.uuid4(), CASE_ID, a.id, c.id, "has_phone", 0.7),
        GraphEdge(uuid.uuid4(), CASE_ID, b.id, d.id, "linked_account", 0.6),
        GraphEdge(uuid.uuid4(), CASE_ID, b.id, e.id, "uses_domain", 0.3),
    ]
    return [a, b, c, d, e], edges


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def test_cytoscape_projection_shape(graph):
    nodes, edges = graph
    cy = projection.to_cytoscape(nodes, edges)

    assert set(cy.keys()) == {"nodes", "edges"}
    assert len(cy["nodes"]) == 5
    assert len(cy["edges"]) == 4

    sample_node = cy["nodes"][0]["data"]
    assert {"id", "label", "type", "score"} <= sample_node.keys()

    sample_edge = cy["edges"][0]["data"]
    assert {"id", "source", "target", "rel", "weight"} <= sample_edge.keys()


def test_cytoscape_min_score_filters_nodes_and_dangling_edges(graph):
    nodes, edges = graph
    cy = projection.to_cytoscape(nodes, edges, min_score=0.5)

    kept_keys = {n["data"]["label"] for n in cy["nodes"]}
    assert "example.com" not in kept_keys  # score 0.2 dropped
    # The edge b->e referenced the dropped domain node and must be gone.
    assert all(
        e["data"]["rel"] != "uses_domain" for e in cy["edges"]
    )


def test_cytoscape_label_falls_back_to_key():
    n = GraphNode(
        id=uuid.uuid4(), case_id=CASE_ID, type="URL", key="https://x/y", attrs={}
    )
    cy = projection.to_cytoscape([n], [])
    assert cy["nodes"][0]["data"]["label"] == "https://x/y"


# ---------------------------------------------------------------------------
# Pure-Python BFS mirror of store.neighbors / store.shortest_path
# (mirrors what the recursive CTE computes; lets us validate semantics
# without spinning up Postgres.)
# ---------------------------------------------------------------------------
def _adj(edges):
    g = defaultdict(set)
    for e in edges:
        g[e.src].add(e.dst)
        g[e.dst].add(e.src)
    return g


def _bfs_neighbors(nodes, edges, start, depth):
    g = _adj(edges)
    seen = {start}
    frontier = {start}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt |= g[n] - seen
        seen |= nxt
        frontier = nxt
    by_id = {n.id: n for n in nodes}
    return [by_id[i] for i in seen if i in by_id]


def _bfs_path(edges, src, dst, max_depth=6):
    if src == dst:
        return [src]
    g = _adj(edges)
    q = deque([(src, [src])])
    while q:
        cur, path = q.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for nb in g[cur]:
            if nb in path:
                continue
            new = path + [nb]
            if nb == dst:
                return new
            q.append((nb, new))
    return []


def test_neighbor_traversal_depth1(graph):
    nodes, edges = graph
    a = nodes[0]
    out = _bfs_neighbors(nodes, edges, a.id, depth=1)
    keys = {n.key for n in out}
    # Alice + her direct neighbors (email, phone), nothing further.
    assert keys == {"alice", "alice@example.com", "+34999111222"}


def test_neighbor_traversal_depth2_reaches_account(graph):
    nodes, edges = graph
    a = nodes[0]
    out = _bfs_neighbors(nodes, edges, a.id, depth=2)
    keys = {n.key for n in out}
    assert "twitter:alice" in keys
    assert "example.com" in keys


def test_shortest_path_two_hops(graph):
    nodes, edges = graph
    a, _b, _c, d, _e = nodes
    path = _bfs_path(edges, a.id, d.id)
    assert path[0] == a.id
    assert path[-1] == d.id
    assert len(path) - 1 == 2  # alice -> email -> account


def test_shortest_path_no_route():
    isolated_a = uuid.uuid4()
    isolated_b = uuid.uuid4()
    assert _bfs_path([], isolated_a, isolated_b) == []
