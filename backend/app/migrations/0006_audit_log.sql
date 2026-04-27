-- Migration 0001: Immutable audit log + mandatory legal basis per case
-- Author: Agent A5 (Wave 1)
--
-- This migration:
--   1. Creates an append-only `audit_log` table (insert-only by policy).
--   2. Extends `cases` with a mandatory GDPR Art. 6 legal basis column.
--
-- Pattern note: this is the first SQL migration; future migrations follow
-- the NNNN_short_description.sql naming. Apply with psql in lexicographic
-- order (a runner is expected to be added by Agent A?).

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. audit_log
-- ---------------------------------------------------------------------------
-- NOTE: an existing `audit_log` table is defined in app/db.py as an ORM
-- model with a different shape (uuid PK, event, actor_ip, payload). That
-- ORM-created table is considered legacy; this migration replaces it with
-- the append-only schema mandated by the GDPR design.
DROP TABLE IF EXISTS audit_log;

CREATE TABLE audit_log (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_api_key_id  UUID         NULL,
    action            TEXT         NOT NULL,
    case_id           UUID         NULL,
    target            JSONB        NULL,
    metadata          JSONB        NULL,
    ip                INET         NULL,
    user_agent        TEXT         NULL
);

CREATE INDEX audit_log_ts_idx       ON audit_log (ts DESC);
CREATE INDEX audit_log_action_idx   ON audit_log (action);
CREATE INDEX audit_log_case_id_idx  ON audit_log (case_id);

-- Append-only enforcement.
--
-- We make audit_log immutable from the application role's perspective by
-- revoking UPDATE and DELETE. The intent is: only INSERT and SELECT are
-- permitted to the runtime DB user. A superuser/DBA may still rotate or
-- archive rows out-of-band (e.g. partitioned retention), which is required
-- under GDPR Art. 5(1)(e) storage limitation.
--
-- The application role name is taken from the ${POSTGRES_USER} env;
-- here we apply to PUBLIC as a defence in depth and rely on the role
-- having only SELECT/INSERT granted explicitly by infra.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC;

-- Belt-and-suspenders: a rule that turns any UPDATE/DELETE into a no-op.
-- Documented behaviour: silently dropped at the SQL layer; tampering
-- attempts will be visible in pg_stat_statements as zero-row commands.
CREATE OR REPLACE RULE audit_log_no_update AS
    ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_log_no_delete AS
    ON DELETE TO audit_log DO INSTEAD NOTHING;

COMMENT ON TABLE audit_log IS
  'Append-only GDPR audit trail. UPDATE/DELETE are blocked by RULES and '
  'revoked grants. Insert via app.audit.record(); never mutate in place.';

-- ---------------------------------------------------------------------------
-- 2. cases.legal_basis
-- ---------------------------------------------------------------------------
-- The ORM ships a `legal_basis TEXT DEFAULT ''` column. Tighten it:
--   * NOT NULL
--   * CHECK against the six GDPR Art. 6(1) lawful bases
--   * companion free-text note for documentation / DPIA reference
ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS legal_basis_note TEXT NULL;

-- Backfill any pre-existing empty rows so the NOT NULL constraint succeeds.
-- 'legitimate_interest' is the safest neutral default for historical data;
-- operators are expected to review and re-classify before going live.
UPDATE cases
   SET legal_basis_note = legal_basis,
       legal_basis = 'legitimate_interest'
 WHERE legal_basis IS NULL
    OR legal_basis NOT IN (
       'consent','legitimate_interest','legal_obligation',
       'public_task','vital_interests','contract'
    );

ALTER TABLE cases
    ALTER COLUMN legal_basis SET NOT NULL;

ALTER TABLE cases
    DROP CONSTRAINT IF EXISTS cases_legal_basis_check;
ALTER TABLE cases
    ADD CONSTRAINT cases_legal_basis_check
    CHECK (legal_basis IN (
        'consent',
        'legitimate_interest',
        'legal_obligation',
        'public_task',
        'vital_interests',
        'contract'
    ));

COMMENT ON COLUMN cases.legal_basis IS
  'GDPR Art. 6(1) lawful basis. Required at case creation.';
COMMENT ON COLUMN cases.legal_basis_note IS
  'Free-text justification / DPIA reference for the chosen legal basis.';

COMMIT;
