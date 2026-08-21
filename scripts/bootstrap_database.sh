#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and set credentials." >&2
    exit 1
fi

set -a
source .env
set +a

required_vars=(
    POSTGRES_DB
    POSTGRES_USER
    POSTGRES_PASSWORD
    POSTGRES_HOST
    POSTGRES_PORT
)

for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "Missing required environment variable: ${var_name}" >&2
        exit 1
    fi
done

echo "Waiting for PostgreSQL..."
until docker compose -f docker/arch3_ph2_sql.yml \
	exec -T postgres \
	pg_isready \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" >/dev/null 2>&1
do
    sleep 1
done

for file in sql/*.sql
do
    echo "Applying ${file}"
    docker compose -f docker/arch3_ph2_sql.yml \
	    exec -T postgres \
	    psql \
        --set=ON_ERROR_STOP=1 \
        -U "${POSTGRES_USER}" \
        -d "${POSTGRES_DB}" \
        < "${file}"
done

echo "Database bootstrap complete."
