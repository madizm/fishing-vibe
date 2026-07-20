#!/usr/bin/env bash
# Download (when absent) and atomically import the full Geofabrik Hubei PBF.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

SOURCE_URL=${OSM_HUBEI_URL:-https://download.geofabrik.de/asia/china/hubei-latest.osm.pbf}
REFRESH=false
if [[ ${1:-} == "--refresh" ]]; then
  REFRESH=true
  shift
fi
PBF=${1:-data/osm/hubei-latest.osm.pbf}
NEXT_SCHEMA=osm_next
LIVE_SCHEMA=osm
PREVIOUS_SCHEMA=osm_previous

mkdir -p data/osm
if [[ $REFRESH == true ]]; then
  echo "[download] refreshing $SOURCE_URL -> $PBF"
  rm -f "$PBF.download"
  curl -L --fail --retry 3 -o "$PBF.download" "$SOURCE_URL"
  mv "$PBF.download" "$PBF"
elif [[ ! -s "$PBF" ]]; then
  echo "[download] $SOURCE_URL -> $PBF"
  curl -L --fail --retry 3 -C - -o "$PBF.part" "$SOURCE_URL"
  mv "$PBF.part" "$PBF"
fi

PBF_DIR=$(realpath "$(dirname "$PBF")")
EXPECTED_DIR=$(realpath data/osm)
if [[ "$PBF_DIR" != "$EXPECTED_DIR" ]]; then
  echo "PBF must be directly under $EXPECTED_DIR (got $PBF)" >&2
  exit 2
fi
PBF_NAME=$(basename "$PBF")
PBF_SIZE=$(stat -c '%s' "$PBF")
PBF_SHA256=$(sha256sum "$PBF" | cut -d' ' -f1)

if (( PBF_SIZE < 1000000 )); then
  echo "PBF is unexpectedly small ($PBF_SIZE bytes): $PBF" >&2
  exit 2
fi

docker compose up -d postgis >/dev/null
DB_USER=$(docker compose exec -T postgis printenv POSTGRES_USER | tr -d '\r')
DB_NAME=$(docker compose exec -T postgis printenv POSTGRES_DB | tr -d '\r')

psql_db() {
  docker compose exec -T postgis \
    psql -X -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" "$@"
}

cleanup_failed_import() {
  local status=$?
  if (( status != 0 )); then
    echo "[cleanup] import failed; keeping live '$LIVE_SCHEMA' schema unchanged" >&2
    psql_db -c "DROP SCHEMA IF EXISTS $NEXT_SCHEMA CASCADE" >/dev/null || true
  fi
  exit "$status"
}
trap cleanup_failed_import EXIT

psql_db -v db_user="$DB_USER" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
DROP SCHEMA IF EXISTS $NEXT_SCHEMA CASCADE;
CREATE SCHEMA $NEXT_SCHEMA AUTHORIZATION :"db_user";
SQL

echo "[import] full Hubei extract ($PBF_SIZE bytes, sha256=$PBF_SHA256)"
OSM_SCHEMA=$NEXT_SCHEMA docker compose --profile tools run --rm \
  -e OSM_SCHEMA=$NEXT_SCHEMA \
  osm-import \
  --create \
  --slim \
  --drop \
  --cache "${OSM2PGSQL_CACHE_MB:-512}" \
  --number-processes "${OSM2PGSQL_PROCESSES:-4}" \
  --output flex \
  --style /config/hubei-flex.lua \
  --schema "$NEXT_SCHEMA" \
  --middle-schema "$NEXT_SCHEMA" \
  --prefix osm2pgsql \
  "/data/$PBF_NAME"

psql_db \
  -v source_url="$SOURCE_URL" \
  -v source_file="$PBF_NAME" \
  -v source_bytes="$PBF_SIZE" \
  -v source_sha256="$PBF_SHA256" <<'SQL'
CREATE INDEX admin_boundaries_admin_level_idx ON osm_next.admin_boundaries (admin_level);
CREATE INDEX admin_boundaries_osm_id_idx ON osm_next.admin_boundaries (osm_type, osm_id);
CREATE INDEX admin_boundaries_name_trgm_idx ON osm_next.admin_boundaries USING GIN (name gin_trgm_ops) WHERE name IS NOT NULL;
CREATE INDEX admin_boundaries_name_zh_trgm_idx ON osm_next.admin_boundaries USING GIN (name_zh gin_trgm_ops) WHERE name_zh IS NOT NULL;
CREATE INDEX water_bodies_class_idx ON osm_next.water_bodies (water_class);
CREATE INDEX water_bodies_osm_id_idx ON osm_next.water_bodies (osm_type, osm_id);
CREATE INDEX water_bodies_geography_gix ON osm_next.water_bodies USING GIST ((geom::geography));
CREATE INDEX water_bodies_name_trgm_idx ON osm_next.water_bodies USING GIN (name gin_trgm_ops) WHERE name IS NOT NULL;
CREATE INDEX water_bodies_name_zh_trgm_idx ON osm_next.water_bodies USING GIN (name_zh gin_trgm_ops) WHERE name_zh IS NOT NULL;
CREATE INDEX waterways_class_idx ON osm_next.waterways (waterway_class);
CREATE INDEX waterways_osm_id_idx ON osm_next.waterways (osm_type, osm_id);
CREATE INDEX waterways_geography_gix ON osm_next.waterways USING GIST ((geom::geography));
CREATE INDEX waterways_name_trgm_idx ON osm_next.waterways USING GIN (name gin_trgm_ops) WHERE name IS NOT NULL;
CREATE INDEX waterways_name_zh_trgm_idx ON osm_next.waterways USING GIN (name_zh gin_trgm_ops) WHERE name_zh IS NOT NULL;
CREATE INDEX water_features_class_idx ON osm_next.water_features (feature_class);
CREATE INDEX water_features_osm_id_idx ON osm_next.water_features (osm_type, osm_id);

CREATE VIEW osm_next.named_waters AS
SELECT 'water_body'::text AS source_kind, osm_type, osm_id, name, name_zh, alt_name,
       water_class AS feature_class, geom
FROM osm_next.water_bodies
UNION ALL
SELECT 'waterway'::text, osm_type, osm_id, name, name_zh, alt_name,
       waterway_class, geom
FROM osm_next.waterways;

CREATE TABLE osm_next.import_metadata (
  imported_at timestamptz NOT NULL DEFAULT now(),
  source_url text NOT NULL,
  source_file text NOT NULL,
  source_bytes bigint NOT NULL,
  source_sha256 text NOT NULL,
  osm2pgsql_version text NOT NULL
);
INSERT INTO osm_next.import_metadata(source_url, source_file, source_bytes, source_sha256, osm2pgsql_version)
VALUES (:'source_url', :'source_file', :'source_bytes', :'source_sha256', '2.2.0');

DO $$
DECLARE
  table_name text;
  row_count bigint;
  bad_count bigint;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['admin_boundaries', 'water_bodies', 'waterways', 'water_features'] LOOP
    EXECUTE format('SELECT count(*) FROM osm_next.%I', table_name) INTO row_count;
    IF row_count = 0 THEN
      RAISE EXCEPTION 'OSM validation failed: %.% is empty', 'osm_next', table_name;
    END IF;
    EXECUTE format(
      'SELECT count(*) FROM osm_next.%I WHERE geom IS NULL OR ST_SRID(geom) <> 4326 OR NOT ST_IsValid(geom)',
      table_name
    ) INTO bad_count;
    IF bad_count <> 0 THEN
      RAISE EXCEPTION 'OSM validation failed: %.% has % bad geometries', 'osm_next', table_name, bad_count;
    END IF;
  END LOOP;
END $$;

ANALYZE osm_next.admin_boundaries;
ANALYZE osm_next.water_bodies;
ANALYZE osm_next.waterways;
ANALYZE osm_next.water_features;
SQL

# Publish only after import, indexes and validation all succeeded. The schema
# rename is transactional, so readers see either the previous or new snapshot.
psql_db <<SQL
BEGIN;
DROP SCHEMA IF EXISTS $PREVIOUS_SCHEMA CASCADE;
DO \$\$
BEGIN
  IF to_regnamespace('$LIVE_SCHEMA') IS NOT NULL THEN
    ALTER SCHEMA $LIVE_SCHEMA RENAME TO $PREVIOUS_SCHEMA;
  END IF;
END \$\$;
ALTER SCHEMA $NEXT_SCHEMA RENAME TO $LIVE_SCHEMA;
DROP SCHEMA IF EXISTS $PREVIOUS_SCHEMA CASCADE;
COMMIT;
SQL

trap - EXIT

echo "[verified] published schema '$LIVE_SCHEMA'"
psql_db -P pager=off -c "
SELECT 'admin_boundaries' AS table_name, count(*) FROM osm.admin_boundaries
UNION ALL SELECT 'water_bodies', count(*) FROM osm.water_bodies
UNION ALL SELECT 'waterways', count(*) FROM osm.waterways
UNION ALL SELECT 'water_features', count(*) FROM osm.water_features
ORDER BY table_name;"
