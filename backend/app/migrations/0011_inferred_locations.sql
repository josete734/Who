BEGIN;
CREATE TABLE IF NOT EXISTS inferred_locations (
    id BIGSERIAL PRIMARY KEY,
    case_id UUID NOT NULL,
    kind TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    radius_m INT NOT NULL,
    confidence REAL NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_finding_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_inferred_loc_case ON inferred_locations(case_id);
CREATE INDEX IF NOT EXISTS idx_inferred_loc_kind ON inferred_locations(case_id, kind);
COMMIT;
