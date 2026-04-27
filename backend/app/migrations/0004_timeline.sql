-- Migration 0004: Chronological timeline events (Wave 2/B4)
-- Author: Agent B4
--
-- Stores per-case dated events extracted from findings (account
-- creation, breach disclosures, post timestamps, last-seen, etc.).
-- Populated by app.timeline.aggregator.build_timeline.

BEGIN;

CREATE TABLE IF NOT EXISTS timeline_events (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           UUID         NOT NULL,
    ts                TIMESTAMPTZ  NOT NULL,
    kind              TEXT         NOT NULL,
    source_collector  TEXT         NOT NULL,
    label             TEXT         NOT NULL DEFAULT '',
    evidence          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    confidence        REAL         NOT NULL DEFAULT 0.5,
    finding_id        UUID         NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS timeline_events_case_ts_idx
    ON timeline_events (case_id, ts);

CREATE INDEX IF NOT EXISTS timeline_events_kind_idx
    ON timeline_events (kind);

COMMIT;
