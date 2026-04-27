-- Migration 0003: Collector quality table (Wave 2/B7)
-- Author: Agent B7
--
-- Persists admin-tunable per-collector quality weights used by the
-- confidence scoring engine (app.scoring). A row overrides the curated
-- DEFAULT_QUALITY baseline in app/scoring/quality.py.

BEGIN;

CREATE TABLE IF NOT EXISTS collector_quality (
    name        TEXT         PRIMARY KEY,
    weight      REAL         NOT NULL CHECK (weight >= 0.0 AND weight <= 1.0),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE collector_quality IS
    'Admin-tunable per-collector quality weights consumed by app.scoring.';

COMMIT;
