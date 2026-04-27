BEGIN;
ALTER TABLE strava_tokens ALTER COLUMN case_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_strava_tokens_athlete ON strava_tokens(athlete_id) WHERE case_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_strava_tokens_athlete_id ON strava_tokens(athlete_id);
COMMIT;
