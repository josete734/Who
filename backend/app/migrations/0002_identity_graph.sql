-- Migration 0002: Identity graph persistence (Wave 2/B1)
-- Author: Agent B1
--
-- Persists nodes (resolved Entities) and edges (REL between Entities) for
-- per-case identity graphs. Consumed by the entity-resolution engine (A6)
-- and exposed via /api/cases/{id}/graph endpoints.

BEGIN;

-- ---------------------------------------------------------------------------
-- graph_nodes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID         NOT NULL,
    type        TEXT         NOT NULL,
    key         TEXT         NOT NULL,
    attrs       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    score       REAL         NOT NULL DEFAULT 0.0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT graph_nodes_unique UNIQUE (case_id, type, key)
);

CREATE INDEX IF NOT EXISTS graph_nodes_case_id_idx ON graph_nodes (case_id);
CREATE INDEX IF NOT EXISTS graph_nodes_type_idx    ON graph_nodes (type);

-- ---------------------------------------------------------------------------
-- graph_edges
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS graph_edges (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID         NOT NULL,
    src         UUID         NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    dst         UUID         NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    rel         TEXT         NOT NULL,
    weight      REAL         NOT NULL DEFAULT 1.0,
    evidence    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT graph_edges_unique UNIQUE (case_id, src, dst, rel)
);

CREATE INDEX IF NOT EXISTS graph_edges_case_id_idx ON graph_edges (case_id);
CREATE INDEX IF NOT EXISTS graph_edges_src_idx     ON graph_edges (src);
CREATE INDEX IF NOT EXISTS graph_edges_dst_idx     ON graph_edges (dst);

COMMENT ON TABLE graph_nodes IS 'Identity graph nodes (resolved Entities) per case.';
COMMENT ON TABLE graph_edges IS 'Identity graph edges; relations between nodes per case.';

COMMIT;
