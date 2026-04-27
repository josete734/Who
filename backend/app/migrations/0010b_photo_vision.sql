-- Migration 0010b: Vision analysis JSON column on photos (Wave 0 / A0.2).
--
-- Stores the structured output of `app.photos.vision.analyze_photo` (Ollama
-- multimodal). Sibling to 0010_photo_exif.sql; intentionally split so the two
-- agents do not collide on a single ALTER batch.

BEGIN;

ALTER TABLE photos ADD COLUMN IF NOT EXISTS vision jsonb;

COMMIT;
