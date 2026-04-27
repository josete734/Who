-- Migration 0003: Geographic intelligence (Wave 2/B5)
-- Author: Agent B5
--
-- Persists per-case GeoSignals (IP-geo, registry addresses, social
-- places, github timezone hints, EXIF) and a global geocoder cache so
-- live Nominatim calls stay rare and polite.

BEGIN;

-- ---------------------------------------------------------------------------
-- geo_signals: every (lat, lon) hint we have for a case
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_signals (
    id                BIGSERIAL    PRIMARY KEY,
    case_id           UUID         NOT NULL,
    lat               DOUBLE PRECISION NOT NULL,
    lon               DOUBLE PRECISION NOT NULL,
    accuracy_km       REAL         NOT NULL DEFAULT 25.0,
    kind              TEXT         NOT NULL,        -- ip|address|social_place|tz_hint|exif
    source_collector  TEXT         NOT NULL,
    evidence          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    confidence        REAL         NOT NULL DEFAULT 0.5,
    finding_id        UUID         NULL,
    observed_at       TIMESTAMPTZ  NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS geo_signals_case_id_idx ON geo_signals (case_id);
CREATE INDEX IF NOT EXISTS geo_signals_kind_idx    ON geo_signals (kind);

-- Try to enable PostGIS for fast spatial queries; if unavailable, fall
-- back to a BRIN over (lat, lon) which is cheap and still useful.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS postgis;
        -- Add a generated geometry column + GiST index when PostGIS is up.
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'geo_signals' AND column_name = 'geom'
        ) THEN
            EXECUTE 'ALTER TABLE geo_signals
                       ADD COLUMN geom geometry(Point, 4326)
                       GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED';
            EXECUTE 'CREATE INDEX IF NOT EXISTS geo_signals_geom_gist
                       ON geo_signals USING GIST (geom)';
        END IF;
    EXCEPTION WHEN OTHERS THEN
        -- PostGIS unavailable: fall back to a BRIN on (lat, lon).
        EXECUTE 'CREATE INDEX IF NOT EXISTS geo_signals_latlon_brin
                   ON geo_signals USING BRIN (lat, lon)';
    END;
END$$;

-- ---------------------------------------------------------------------------
-- geo_cache: global geocoder result cache (place-name -> lat/lon).
-- Shared across cases; keyed by a sha1 of the normalised query string so
-- we never hammer Nominatim for the same place twice.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo_cache (
    key          TEXT          PRIMARY KEY,
    name         TEXT          NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    accuracy_km  REAL          NOT NULL DEFAULT 5.0,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE geo_signals IS 'Per-case geographic signals (Wave 2/B5).';
COMMENT ON TABLE geo_cache   IS 'Geocoder result cache to avoid Nominatim hammering.';

COMMIT;
