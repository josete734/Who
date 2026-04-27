-- Migration 0003: Auto-pivot cascade engine
-- Author: Agent A8 (Wave 1)
--
-- Adds the `case_pivots` table used by app.pivot.dispatcher to dedupe
-- pivot atoms (emails/phones/domains/usernames/...) extracted from
-- Findings and to record the depth at which each atom entered the
-- cascade.
--
-- Design notes:
--   * UNIQUE(case_id, kind, value) is the dedup key. The dispatcher
--     uses ON CONFLICT DO NOTHING so concurrent extractors are safe.
--   * `depth` is the depth *of this pivot* (0 = original input,
--     1 = derived from a depth-0 finding, etc.). The dispatcher
--     refuses to dispatch beyond the configured max_pivot_depth.
--   * `dispatched_at` is set after collectors are enqueued; NULL means
--     the pivot was recorded but not yet fanned out (e.g. blocked by
--     budget or policy).
--   * `source_finding_id` is intentionally NOT a FK: findings may be
--     pruned or partitioned out of band, and we want pivot history to
--     survive those operations.

BEGIN;

CREATE TABLE IF NOT EXISTS case_pivots (
    id                 UUID         PRIMARY KEY,
    case_id            UUID         NOT NULL,
    kind               TEXT         NOT NULL,
    value              TEXT         NOT NULL,
    source_finding_id  UUID         NULL,
    depth              INTEGER      NOT NULL DEFAULT 0,
    confidence         REAL         NOT NULL DEFAULT 0.7,
    dispatched_at      TIMESTAMPTZ  NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT case_pivots_kind_check CHECK (kind IN (
        'username', 'email', 'phone', 'domain', 'url',
        'full_name', 'photo_url', 'profile_id', 'ip', 'crypto_address'
    )),
    CONSTRAINT case_pivots_depth_check CHECK (depth >= 0 AND depth <= 10),
    CONSTRAINT case_pivots_confidence_check CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS case_pivots_case_kind_value_uniq
    ON case_pivots (case_id, kind, value);

CREATE INDEX IF NOT EXISTS case_pivots_case_id_idx
    ON case_pivots (case_id);

CREATE INDEX IF NOT EXISTS case_pivots_source_finding_idx
    ON case_pivots (source_finding_id);

CREATE INDEX IF NOT EXISTS case_pivots_pending_idx
    ON case_pivots (case_id) WHERE dispatched_at IS NULL;

COMMENT ON TABLE  case_pivots IS
  'Deduplicated pivot atoms discovered during a case run. Drives the auto-pivot cascade engine.';
COMMENT ON COLUMN case_pivots.kind IS
  'One of: username, email, phone, domain, url, full_name, photo_url, profile_id, ip, crypto_address.';
COMMENT ON COLUMN case_pivots.depth IS
  '0 = seeded from initial SearchInput; N = derived from a depth-(N-1) finding.';
COMMENT ON COLUMN case_pivots.dispatched_at IS
  'Set when collectors targeting this atom have been enqueued. NULL = recorded but not yet fanned out.';

COMMIT;
