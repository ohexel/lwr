# Optional containerized Dagster

This branch provides an optional background Dagster deployment for demos and
local monitoring. It does not replace the normal host-based installation,
bootstrap, forecast command, or PostgreSQL volume.

One container runs `dagster dev`, including its interface, daemon, code server,
and manually enabled forecast sensor. The existing PostgreSQL service stays in
its original container.

## Prerequisites

Complete the normal installation and operational bootstrap first. The project
root should already contain `.env`, populated `data/`, and `.dagster_home/`.

Stop any host-based `dagster dev` process before starting the container. Running
two Dagster daemons against the same instance can duplicate sensor processing,
and both interfaces would otherwise compete for port 3000.

The container runs as user/group `1000:1000` by default so files created in the
mounted repository do not belong to root. If your account uses different
numeric identifiers, set them in the same shell before using Compose:

```bash
export DAGSTER_UID="$(id -u)"
export DAGSTER_GID="$(id -g)"
```

## Start the interface and background daemon

From the project root:

```bash
docker compose \
  --env-file .env \
  -f docker/postgres.yml \
  -f docker/dagster.yml \
  up -d --build
```

The first start builds a Python 3.11 image using the exact locked dependencies
in `uv.lock`. Subsequent starts reuse the existing image unless dependencies or
the Dockerfile change.

Open [http://localhost:3000](http://localhost:3000). Existing materializations
and run history remain available because `/app/.dagster_home` is the same
project-local directory used by the host.

The forecast availability sensor remains **stopped by default**. Start
`dwd_icon_d2_ruc_availability_sensor` manually in the Dagster interface when
background ingestion is desired.

The normal host-based manual forecast command continues to work:

```bash
uv run --env-file .env python -m src.run_forecast \
  --run-time "$(date -u -d '2 hours ago' +%Y%m%dT%H00)"
```

## Inspect and control the container

Follow Dagster logs:

```bash
docker compose \
  --env-file .env \
  -f docker/postgres.yml \
  -f docker/dagster.yml \
  logs -f dagster
```

Check container resource use:

```bash
docker stats --no-stream
```

Stop Dagster without stopping PostgreSQL or deleting any data:

```bash
docker compose \
  --env-file .env \
  -f docker/postgres.yml \
  -f docker/dagster.yml \
  stop dagster
```

Start it again without rebuilding:

```bash
docker compose \
  --env-file .env \
  -f docker/postgres.yml \
  -f docker/dagster.yml \
  up -d dagster
```

## Optional local limits

The container defaults to two CPU cores, 2 GiB of memory, and interface port
3000. Override these values in `.env` or the current shell when needed:

```bash
export DAGSTER_CPUS=1.5
export DAGSTER_MEMORY_LIMIT=1500m
export DAGSTER_PORT=3001
```

Recreate the service after changing limits:

```bash
docker compose \
  --env-file .env \
  -f docker/postgres.yml \
  -f docker/dagster.yml \
  up -d dagster
```

These limits constrain the application container only; PostgreSQL continues to
use its existing service configuration. The interface is published only on
`127.0.0.1`.

## Runtime boundaries

- PostgreSQL remains persistent in the existing Compose volume.
- Containerized Dagster reaches PostgreSQL at `postgres:5432`; the host keeps
  using the `POSTGRES_HOST` and published `POSTGRES_PORT` configured in `.env`.
- The project is mounted at `/app`, sharing source code, retained `data/`, and
  `.dagster_home/` with host commands.
- Locked Python dependencies live at `/opt/capstone-venv`, outside that mount;
  the host's `.venv` is neither reused nor overwritten.
- The image build sends only dependency metadata and the Dockerfile, not raw
  source datasets, retained forecasts, historical archives, or snapshots.
- This optional deployment does not enable the sensor automatically or change
  any forecast, analytical, or data-quality contract.
