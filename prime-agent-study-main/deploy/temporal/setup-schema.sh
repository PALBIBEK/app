#!/bin/sh
# Create (or migrate) Temporal's persistence and visibility schemas in Postgres.
# Idempotent: safe to run on every `docker compose up`.
set -eu
: "${POSTGRES_SEEDS:?}" "${POSTGRES_USER:?}" "${SQL_PASSWORD:?}"
PORT="${DB_PORT:-5432}"
SCHEMA_DIR=/etc/temporal/schema/postgresql/v12

until nc -z -w 5 "$POSTGRES_SEEDS" "$PORT"; do echo "waiting for postgres..."; sleep 2; done

sql() { temporal-sql-tool --plugin postgres12 --ep "$POSTGRES_SEEDS" -p "$PORT" -u "$POSTGRES_USER" "$@"; }

for db in temporal temporal_visibility; do
  dir=temporal; [ "$db" = temporal_visibility ] && dir=visibility
  sql --db "$db" create 2>/dev/null || echo "database $db already exists"
  # setup-schema -v 0.0 is a no-op once the version table exists; update-schema migrates.
  sql --db "$db" setup-schema -v 0.0 2>/dev/null || true
  sql --db "$db" update-schema -d "$SCHEMA_DIR/$dir/versioned"
done
echo "temporal schema ready"
