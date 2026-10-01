-- The geometry column is optional at application level, but production uses
-- PostGIS for spatial indexing and future point location queries.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE INDEX IF NOT EXISTS ix_points_geom_gist ON points USING GIST (geom_geometry);
