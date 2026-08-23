#!/usr/bin/env bash
set -euo pipefail

echo "This removes the local PostgreSQL volume and all database state."
read -r -p "Type RESET to continue: " confirmation

if [[ "${confirmation}" != "RESET" ]]; then
    echo "Reset cancelled."
    exit 1
fi

docker compose --env-file .env -f docker/postgres.yml down --volumes
docker compose --env-file .env -f docker/postgres.yml up -d postgres

echo "Database volume recreated."
echo "Run: ./scripts/bootstrap_database.sh"
