-- Migration 0002: Resolved entities + cross-collector source attribution
-- Author: Agent A6 (Wave 1)
--
-- Adds two tables consumed by app/entity_resolution/engine.py:
--   * entities         — one row per resolved/deduped entity
--   * entity_sources   — many-to-many: which findings contributed
--
-- Apply with psql in lexicographic order after 0001_*.

BEGIN;

CREATE TABLE IF NOT EXISTS entities (
    id        UUID PRIMARY KEY,
    case_id   UUID NOT NULL,
    type      TEXT NOT NULL CHECK (type IN (
        'Person','Account','Email','Phone','Domain',
        'URL','Photo','Location','Document'
    )),
    value     TEXT NOT NULL,
    attrs     JSONB NOT NULL DEFAULT '{}'::jsonb,
    score     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lookup pattern: list entities of a given type in a case, optionally by value.
CREATE INDEX IF NOT EXISTS entities_case_type_value_idx
    ON entities (case_id, type, value);
CREATE INDEX IF NOT EXISTS entities_case_score_idx
    ON entities (case_id, score DESC);

CREATE TABLE IF NOT EXISTS entity_sources (
    id           BIGSERIAL PRIMARY KEY,
    entity_id    UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    finding_id   UUID NULL,    -- nullable: synthetic links (e.g. gravatar inferred)
    collector    TEXT NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entity_sources_entity_idx
    ON entity_sources (entity_id);
CREATE INDEX IF NOT EXISTS entity_sources_finding_idx
    ON entity_sources (finding_id);

COMMENT ON TABLE entities IS
  'Resolved OSINT entities (Person, Account, Email, Phone, ...) deduped '
  'across collectors. score is noisy-OR aggregation of source confidences.';
COMMENT ON TABLE entity_sources IS
  'Per-finding attribution for an entity. Used by the /entities/graph endpoint '
  'to draw co-occurrence edges (entities sharing a finding).';

COMMIT;
