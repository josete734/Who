-- Migration 0003: Certificate Transparency watchlist
-- Author: Agent C9 (Wave 3)
--
-- Adds the ct_watchlist table consumed by app/ct_watcher/runner.py.
-- One row per (domain) under continuous CT monitoring; last_seen_id is
-- the certspotter issuance id high-water mark used to fetch only deltas.
--
-- Apply with psql in lexicographic order after 0002_*.

BEGIN;

CREATE TABLE IF NOT EXISTS ct_watchlist (
    domain        TEXT PRIMARY KEY,
    case_id       UUID NOT NULL,
    last_seen_id  TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ct_watchlist_case_idx
    ON ct_watchlist (case_id);

COMMENT ON TABLE ct_watchlist IS
  'Domains under continuous Certificate Transparency monitoring. '
  'Updated incrementally by ct_watcher.runner using certspotter ?after=last_seen_id.';

COMMIT;
