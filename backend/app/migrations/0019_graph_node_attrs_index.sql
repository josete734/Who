-- Migration 0019: GIN index on graph_nodes.attrs for analytics queries.
--
-- Wave 4 writes per-node analytics (centrality scores, community_id) into
-- ``graph_nodes.attrs`` so the existing ``GET /graph`` endpoint surfaces them
-- without re-computing on every read. A GIN index over the JSONB column lets
-- the UI ask for "all hubs" or "all members of community 3" cheaply.
BEGIN;

CREATE INDEX IF NOT EXISTS idx_graph_nodes_attrs_gin
    ON graph_nodes USING GIN (attrs);

COMMIT;
