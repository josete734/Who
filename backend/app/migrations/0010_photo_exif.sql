-- Migration 0010: EXIF metadata columns on photos (Wave 0 / A0.1).
--
-- Stores GPS coordinates, capture timestamp, camera/lens/software fields
-- and the full EXIF dict (JSON) extracted by `app.photos.exif.parse_exif`.
--
-- NOTE: a sibling migration 0010b_photo_vision.sql (A0.2) will add
-- `vision jsonb`. Do NOT add it here to avoid collision.

BEGIN;

ALTER TABLE photos ADD COLUMN IF NOT EXISTS gps_lat       double precision;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS gps_lon       double precision;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS taken_at      timestamptz;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS camera_make   text;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS camera_model  text;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS lens_model    text;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS software      text;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS exif          jsonb;

CREATE INDEX IF NOT EXISTS photos_taken_at_idx ON photos (taken_at);
CREATE INDEX IF NOT EXISTS photos_gps_idx      ON photos (gps_lat, gps_lon);

COMMIT;
