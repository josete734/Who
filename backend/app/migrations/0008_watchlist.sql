-- Migration 0008: Watchlist scheduler (Wave 4 / D4)
BEGIN;

CREATE TABLE IF NOT EXISTS watchlist (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner              TEXT         NOT NULL DEFAULT '',
    query_inputs       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    schedule_cron      TEXT         NOT NULL DEFAULT '0 * * * *',
    last_run_at        TIMESTAMPTZ  NULL,
    last_results_hash  TEXT         NULL,
    enabled            BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_enabled ON watchlist (enabled) WHERE enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_watchlist_owner   ON watchlist (owner);

COMMIT;
