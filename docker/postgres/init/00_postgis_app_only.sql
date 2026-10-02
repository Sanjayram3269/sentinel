-- Keep the database aligned with the backend's application schema.
-- The default PostGIS image includes Tiger geocoder/topology schemas,
-- but the SENTINEL app only needs the core PostGIS extension.
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP SCHEMA IF EXISTS tiger_data CASCADE;
DROP SCHEMA IF EXISTS tiger CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
DROP SCHEMA IF EXISTS topology CASCADE;
CREATE EXTENSION IF NOT EXISTS postgis;
