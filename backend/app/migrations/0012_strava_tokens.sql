BEGIN;
CREATE TABLE IF NOT EXISTS strava_tokens (
  id BIGSERIAL PRIMARY KEY,
  case_id UUID NOT NULL,
  athlete_id BIGINT NOT NULL,
  access_token_enc TEXT NOT NULL,
  refresh_token_enc TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_strava_tokens_case ON strava_tokens(case_id);
COMMIT;
