-- Migration 0005: Rules engine + alerts (Wave 4 / D5)
--
-- Adds:
--   * rules  -- JSON-DSL alerting rules (see app/rules/dsl.py)
--   * alerts -- materialized rule firings, ack-able by analysts
--
-- Built-in defaults (see app/rules/defaults.py) are upserted by name so
-- this migration is safe to run multiple times.

BEGIN;

CREATE TABLE IF NOT EXISTS rules (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT         NOT NULL UNIQUE,
    dsl         JSONB        NOT NULL,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rules_enabled ON rules (enabled) WHERE enabled = TRUE;

CREATE TABLE IF NOT EXISTS alerts (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID         NULL,
    rule_id     UUID         NULL REFERENCES rules(id) ON DELETE SET NULL,
    level       TEXT         NOT NULL DEFAULT 'info',
    message     TEXT         NOT NULL,
    payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    acked_at    TIMESTAMPTZ  NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_case_id    ON alerts (case_id);
CREATE INDEX IF NOT EXISTS idx_alerts_rule_id    ON alerts (rule_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unacked    ON alerts (created_at DESC) WHERE acked_at IS NULL;

-- Seed built-in defaults. Keep in sync with app/rules/defaults.py.
INSERT INTO rules (name, dsl, enabled) VALUES
  ('leaked_password',
   '{"when":"collector.result","if":{"collector":{"eq":"hibp_passwords"},"payload.hits":{"gt":0}},"then":{"alert":"high","message":"Leaked password detected for {{ payload.email or payload.identifier or ''subject'' }} ({{ payload.hits }} hit(s))."}}'::jsonb,
   TRUE),
  ('darkweb_hit',
   '{"when":"collector.result","if":{"collector":{"eq":"ahmia"},"payload.hits":{"gt":0}},"then":{"alert":"high","message":"Darkweb mention found via ahmia for query ''{{ payload.query }}'' ({{ payload.hits }} result(s))."}}'::jsonb,
   TRUE),
  ('ct_new_cert',
   '{"when":"ct.new_cert","if":{"payload.domain":{"regex":".+"}},"then":{"alert":"medium","message":"New TLS certificate observed for {{ payload.domain }} (issuer: {{ payload.issuer or ''unknown'' }})."}}'::jsonb,
   TRUE),
  ('breach_email',
   '{"when":"collector.result","if":{"collector":{"in":["dehashed","hibp","breach_index"]},"payload.email":{"regex":".+@.+"}},"then":{"alert":"high","message":"Email {{ payload.email }} found in breach source ''{{ payload.collector }}''."}}'::jsonb,
   TRUE),
  ('contradictory_identities',
   '{"when":"entity.updated","if":{"payload.real_name_count":{"gt":1}},"then":{"alert":"high","message":"Entity {{ payload.entity_id }} has {{ payload.real_name_count }} contradictory real_name values: {{ payload.real_names }}."}}'::jsonb,
   TRUE),
  ('high_confidence_entity',
   '{"when":"entity.resolved","if":{"payload.score":{"gt":0.9}},"then":{"alert":"medium","message":"High-confidence entity match (score={{ payload.score }}) for entity {{ payload.entity_id }}."}}'::jsonb,
   TRUE)
ON CONFLICT (name) DO NOTHING;

COMMIT;
