-- Migration 0018: Strava heatmap CloudFront cookie storage.
--
-- The heatmap-authed endpoint (heatmap-external-{a,b,c}.strava.com) demands
-- three CloudFront cookies that Strava issues only via the web UI. The
-- operator pastes them into /api/integrations/strava/heatmap-cookie; this
-- migration adds the columns we persist them into (encrypted with Fernet via
-- app.integrations.strava_oauth.encrypt).
BEGIN;

ALTER TABLE strava_tokens
  ADD COLUMN IF NOT EXISTS cloudfront_cookie TEXT,
  ADD COLUMN IF NOT EXISTS cloudfront_cookie_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_strava_tokens_cf_cookie
  ON strava_tokens (cloudfront_cookie_updated_at DESC NULLS LAST)
  WHERE cloudfront_cookie IS NOT NULL;

COMMIT;
