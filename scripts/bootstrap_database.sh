#!/usr/bin/env bash

# Apply the current canonical schema exactly once. Historical development
# migrations contain deliberate TRUNCATE statements and are never replayed.
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if [[ ! -f .env ]]; then
    echo "Missing .env. Run: cp .env.example .env" >&2
    exit 1
fi

set -a
source .env
set +a

required_variables=(
    POSTGRES_HOST
    POSTGRES_PORT
    POSTGRES_DB
    POSTGRES_USER
    POSTGRES_PASSWORD
)

for variable_name in "${required_variables[@]}"; do
    if [[ -z "${!variable_name:-}" ]]; then
        echo "Missing required environment variable: ${variable_name}" >&2
        exit 1
    fi
done

postgres_exec() {
    docker compose \
        --env-file .env \
        -f docker/postgres.yml \
        exec -T postgres "$@"
}

psql_command() {
    postgres_exec psql \
        --set=ON_ERROR_STOP=1 \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        "$@"
}

echo "Waiting for PostgreSQL..."
postgres_ready=0

for _attempt in {1..120}; do
    if postgres_exec pg_isready \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        >/dev/null 2>&1
    then
        postgres_ready=1
        break
    fi

    sleep 1
done

if [[ "$postgres_ready" != 1 ]]; then
    echo "PostgreSQL did not become ready within 120 seconds." >&2
    exit 1
fi

schema_count="$(
    psql_command \
        --tuples-only \
        --no-align \
        --command "
            SELECT COUNT(*)
            FROM pg_namespace
            WHERE nspname IN ('raw', 'normalized', 'analytical')
        "
)"

case "$schema_count" in
    0)
        echo "Applying canonical project schema: sql/bootstrap_schema.sql"
        psql_command --single-transaction < sql/bootstrap_schema.sql
        ;;
    3)
        schema_is_compatible="$(
            psql_command \
                --tuples-only \
                --no-align \
                --command "
                    SELECT (
                        to_regclass('normalized.plr') IS NOT NULL
                        AND to_regclass(
                            'analytical.hostrada_plr_hourly_reference'
                        ) IS NOT NULL
                        AND to_regclass(
                            'analytical.hostrada_berlin_hourly_reference'
                        ) IS NOT NULL
                        AND to_regclass(
                            'analytical.current_plr_weather_context'
                        ) IS NOT NULL
                        AND to_regprocedure(
                            'analytical.hostrada_reference_expected_hours(integer)'
                        ) IS NOT NULL
                    )::INTEGER
                "
        )"

        if [[ "$schema_is_compatible" != 1 ]]; then
            echo "Existing project schemas do not match the canonical contract." >&2
            echo "Refusing to replay migrations or modify existing data." >&2
            exit 1
        fi

        echo "Canonical project schema already exists; preserving all data."
        ;;
    *)
        echo "Found ${schema_count} of the three required project schemas." >&2
        echo "Refusing to modify a partially initialized database." >&2
        exit 1
        ;;
esac

snapshot_gate_installed="$(
    psql_command \
        --tuples-only \
        --no-align \
        --command "
            SELECT (
                to_regprocedure(
                    'analytical.check_hostrada_reference_snapshot(text,integer)'
                ) IS NOT NULL
            )::INTEGER
        "
)"

if [[ "$snapshot_gate_installed" != 1 ]]; then
    echo "Installing the additive HOSTRADA snapshot validation function."
    psql_command --single-transaction \
        < sql/037_hostrada_reference_snapshot_validation.sql
fi

psql_command --command "
    SELECT
        current_database() AS database_name,
        postgis_version() AS postgis_version,
        'canonical schema ready' AS status
"

echo "Database bootstrap complete."
