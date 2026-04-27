-- Migration 0004: Local combo-list breach index (Wave 3 / C10)
-- Author: Agent C10
--
-- Stores *only* GDPR-safe artifacts derived from imported combo lists:
--   * email_hash  : SHA-256 of the normalized (lowercased, trimmed) email.
--   * username    : public handle if present in the source row.
--   * hint_password_class : non-reversible classification
--                   (e.g. "len:8-12;had_digit;had_symbol"). Plaintext
--                   passwords are NEVER persisted.
--   * source / observed_at: provenance metadata.
--
-- Retention policy (GDPR): rows are stored to support the data subject's
-- right to be informed about exposure. The owning controller MUST:
--   1. Document the legal basis (legitimate interest / consent) in the
--      ROPA before importing any combo list.
--   2. Apply a TTL: rows older than `retention_days` (default 365) MUST be
--      purged by a scheduled job. See backend/scripts/import_combo.py.
--   3. Honor erasure requests: deleting by email_hash is sufficient.

BEGIN;

CREATE TABLE IF NOT EXISTS breach_records (
    id                   BIGSERIAL    PRIMARY KEY,
    source               TEXT         NOT NULL,
    email_hash           BYTEA        NOT NULL,
    username             TEXT         NULL,
    hint_password_class  TEXT         NULL,
    observed_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS breach_records_email_hash_idx
    ON breach_records USING hash (email_hash);

CREATE INDEX IF NOT EXISTS breach_records_username_trgm_idx
    ON breach_records USING gin (username gin_trgm_ops);

CREATE INDEX IF NOT EXISTS breach_records_observed_at_idx
    ON breach_records (observed_at);

COMMENT ON TABLE breach_records IS
  'GDPR-safe local index of imported combo lists. Plaintext passwords are never stored.';
COMMENT ON COLUMN breach_records.email_hash IS
  'SHA-256 of lowercased, trimmed email. 32 raw bytes.';
COMMENT ON COLUMN breach_records.hint_password_class IS
  'Non-reversible password classification (length bucket + character classes).';

COMMIT;
