-- Migration: 0009_tenancy
-- Wave 5/E3: organizations, teams, memberships, per-case ACLs.
-- Idempotent: safe to re-run.

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- orgs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orgs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT        NOT NULL,
    slug        TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_orgs_slug ON orgs (slug);

-- ---------------------------------------------------------------------------
-- teams
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id      UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS ix_teams_org_id ON teams (org_id);

-- ---------------------------------------------------------------------------
-- memberships
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE tenancy_role AS ENUM ('owner', 'admin', 'investigator', 'viewer');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS memberships (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id      UUID         NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     TEXT         NOT NULL,
    team_id     UUID         NULL REFERENCES teams(id) ON DELETE SET NULL,
    role        tenancy_role NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, team_id)
);

CREATE INDEX IF NOT EXISTS ix_memberships_org_id  ON memberships (org_id);
CREATE INDEX IF NOT EXISTS ix_memberships_user_id ON memberships (user_id);

-- ---------------------------------------------------------------------------
-- case_access (per-case ACL overrides)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_access (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id     UUID         NOT NULL,
    org_id      UUID         NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     TEXT         NULL,
    team_id     UUID         NULL REFERENCES teams(id) ON DELETE SET NULL,
    role        tenancy_role NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_case_access_case_id ON case_access (case_id);
CREATE INDEX IF NOT EXISTS ix_case_access_org_id  ON case_access (org_id);
CREATE INDEX IF NOT EXISTS ix_case_access_user_id ON case_access (user_id);

-- ---------------------------------------------------------------------------
-- cases.org_id  (nullable for backward compat; backfill out-of-band)
-- ---------------------------------------------------------------------------
ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS org_id UUID NULL;
CREATE INDEX IF NOT EXISTS ix_cases_org_id ON cases (org_id);

-- ---------------------------------------------------------------------------
-- api_keys.org_id (nullable; binds an api key to an org)
-- ---------------------------------------------------------------------------
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS org_id UUID NULL;
CREATE INDEX IF NOT EXISTS ix_api_keys_org_id ON api_keys (org_id);

COMMIT;
