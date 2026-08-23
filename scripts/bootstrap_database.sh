#!/usr/bin/env bash

# Run only against a fresh/reset database.
# This is not intended to be replayed against a populated database.

set -euo pipefail

if [[ ! -f .env ]]; then
	echo "Missing .env." >&2
	exit 1
fi

set -a 
source .env
set +a

required_vars=(
	POSTGRES_DB
	POSTGRES_USER
	POSTGRES_PASSWORD
)

for var_name in "${required_vars[@]}"; do
	if [[ -z "${!var_name:-}" ]]; then
		echo "Missing required environment variable: ${var_name}" >&2
		exit 1
	fi
done

echo "Waiting for PostgreSQL..."
until docker compose \
	--env-file .env \
	-f docker/postgres.yml \
	exec -T postgres \
	pg_isready \
	-U "${POSTGRES_USER}" \
	-d "${POSTGRES_DB}" \
	>/dev/null 2>&1
do
    sleep 1
done

for file in sql/[0-9][0-9][0-9]_*.sql
do
    echo "Applying ${file}"
    docker compose \
	    --env-file .env \
	    -f docker/postgres.yml \
	    exec -T postgres \
	    psql \
	    --set=ON_ERROR_STOP=1 \
	    -U "${POSTGRES_USER}" \
	    -d "${POSTGRES_DB}" \
	    < "${file}"
done

echo "Database bootstrap complete."
