-- Migration 0002: photo aggregator + face clustering tables (Wave 2 / B3).
--
-- Owns two tables:
--   * photos          - one row per downloaded image (dedup by (case_id, sha256))
--   * photo_clusters  - one row per detected cluster (face or pHash)
--
-- The `cluster_id` FK on `photos` is intentionally a plain UUID (no FK
-- constraint) so we can write photos before the cluster row is created
-- and avoid circular dependencies with `representative_photo_id`.

BEGIN;

CREATE TABLE IF NOT EXISTS photos (
    id                  UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id             UUID         NOT NULL,
    source_finding_id   UUID         NULL,
    url                 TEXT         NOT NULL,
    sha256              TEXT         NOT NULL,
    phash               TEXT         NULL,
    width               INT          NULL,
    height              INT          NULL,
    mime                TEXT         NULL,
    downloaded_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    face_encoding       BYTEA        NULL,
    cluster_id          UUID         NULL,
    CONSTRAINT photos_case_sha_uniq UNIQUE (case_id, sha256)
);

CREATE INDEX IF NOT EXISTS photos_case_idx        ON photos (case_id);
CREATE INDEX IF NOT EXISTS photos_cluster_idx     ON photos (cluster_id);
CREATE INDEX IF NOT EXISTS photos_finding_idx     ON photos (source_finding_id);
CREATE INDEX IF NOT EXISTS photos_phash_idx       ON photos (phash);

CREATE TABLE IF NOT EXISTS photo_clusters (
    id                       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id                  UUID         NOT NULL,
    label                    TEXT         NULL,
    score                    DOUBLE PRECISION NULL,
    count                    INT          NOT NULL DEFAULT 0,
    representative_photo_id  UUID         NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS photo_clusters_case_idx ON photo_clusters (case_id);

COMMENT ON TABLE photos IS
  'Downloaded photo blobs (refs only) with pHash + optional face encoding.';
COMMENT ON TABLE photo_clusters IS
  'Groups of related photos (same face or near-duplicate pHash).';

COMMIT;
