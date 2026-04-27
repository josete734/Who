-- Migration 0007: Webhook subscriptions + delivery log (Wave 4 / D4)
BEGIN;

CREATE TABLE IF NOT EXISTS webhooks (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    url         TEXT         NOT NULL,
    secret      TEXT         NOT NULL,
    events      JSONB        NOT NULL DEFAULT '[]'::jsonb,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhooks_enabled ON webhooks (enabled) WHERE enabled = TRUE;

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id  UUID         NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event       TEXT         NOT NULL,
    status      TEXT         NOT NULL DEFAULT 'pending',
    attempts    INT          NOT NULL DEFAULT 0,
    last_error  TEXT         NULL,
    payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_wid     ON webhook_deliveries (webhook_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_event   ON webhook_deliveries (event);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created ON webhook_deliveries (created_at DESC);

COMMIT;
