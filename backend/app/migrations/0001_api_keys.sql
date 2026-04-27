-- Migration: 0001_api_keys
-- Creates the api_keys table for Auth v2.
-- Idempotent: safe to run on top of `init_db()` SQLAlchemy create_all.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS api_keys (
    id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                   VARCHAR(128) NOT NULL,
    lookup_hash            VARCHAR(64)  NOT NULL UNIQUE,
    hash                   TEXT         NOT NULL,
    scopes                 JSONB        NOT NULL DEFAULT '[]'::jsonb,
    rate_limit_per_minute  INTEGER      NOT NULL DEFAULT 60,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at           TIMESTAMPTZ,
    revoked_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_api_keys_lookup_hash ON api_keys (lookup_hash);
CREATE INDEX IF NOT EXISTS ix_api_keys_revoked_at  ON api_keys (revoked_at);
