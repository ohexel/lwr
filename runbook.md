# Operational runbook

This document describes the normal installation and operation of the Berlin
neighborhood-temperature pipeline. Reconstructing the 1995–2025 historical archive
is a separate, optional procedure documented in
[docs/historical-rebuild.md](docs/historical-rebuild.md).

## Operating requirements

- Docker Engine and the Docker Compose plugin.
- `uv` and Python 3.11.
- The repository's tracked `pyproject.toml`, `uv.lock`, `.env.example`,
  `docker/postgres.yml`, canonical SQL schema, and HOSTRADA reference manifest.
- The externally distributed
  [HOSTRADA reference archive](https://www.ohexel.com/files/hostrada-reference-1995-2025.pgcustom).
- Optionally, the externally distributed static-input archive.
- A persistent Docker volume for PostgreSQL and a persistent `DAGSTER_HOME`
  directory for Dagster run history.

The first verified clean-room installation completed in approximately ten
minutes. It created 542,040 ICON grid cells and imported approximately
4.76 million reference rows without reconstructing historical temperature.

## First installation

From the repository root:

```bash
cp .env.example .env
```

Edit `.env`, including `POSTGRES_PASSWORD`. Do not commit `.env`.

```bash
uv sync --frozen
```

```bash
docker compose --env-file .env -f docker/postgres.yml up -d
```

```bash
export DAGSTER_HOME="$PWD/.dagster_home"
```

Verify the external HOSTRADA archive before starting the expensive spatial
bootstrap:

```bash
uv run --env-file .env python -m src.hostrada_snapshot verify \
  --archive /path/to/hostrada-reference-1995-2025.pgcustom
```

Initialize all operational dependencies:

```bash
uv run --env-file .env python -m src.bootstrap \
  --reference-archive /path/to/hostrada-reference-1995-2025.pgcustom
```

To preserve automatic acquisition while providing a fallback:

```bash
uv run --env-file .env python -m src.bootstrap \
  --reference-archive /path/to/hostrada-reference-1995-2025.pgcustom \
  --static-snapshot /path/to/static-inputs.tar.xz
```

To avoid static-source network requests entirely:

```bash
uv run --env-file .env python -m src.bootstrap \
  --reference-archive /path/to/hostrada-reference-1995-2025.pgcustom \
  --static-snapshot /path/to/static-inputs.tar.xz \
  --offline
```

`--offline` applies to static data acquisition, not the initial Docker/Python
dependency installation or subsequent live DWD forecast processing.

The successful JSON summary must report:

```json
{
  "status": "ready",
  "hostrada_snapshot": {
    "status": "imported"
  },
  "plr_display_names": {
    "status": "installed",
    "plr_count": 542
  },
  "weather_sensor_default_status": "STOPPED"
}
```

An already initialized installation reports
`"hostrada_snapshot": {"status": "already_installed"}` instead. The PLR-name
lookup likewise reports `"already_installed"` on subsequent runs.

### Expected static and reference state

| Contract | Expected |
| --- | ---: |
| Berlin PLRs | 542 |
| Analyst-facing PLR display names | 542 |
| Raw population rows | 542 |
| Accepted population rows | 540 |
| Rejected population rows | 2 |
| ICON grid cells | 542,040 |
| ICON-to-PLR bridge rows | 1,678 |
| Berlin forecast-mask cells | 465 |
| PLR historical-reference rows | 4,747,920 |
| Berlin historical-reference rows | 8,760 |
| Included historical Berlin observations | 271,559 |

The bridge and forecast-mask counts reflect the committed geography and the
default 5,000 m mask buffer. Deliberately changing the spatial configuration
may change those values.

## Manual forecast processing

Inspect supported arguments and lead times:

```bash
uv run --env-file .env python -m src.run_forecast --help
```

Process an explicit UTC model run:

```bash
uv run --env-file .env python -m src.run_forecast \
  --run-time 20260824T1600 \
  --lead-time PT000H00M
```

The example timestamp documents the format; old example runs should not be
expected to remain available upstream. On Debian/GNU/Linux, choose a recent
candidate dynamically:

```bash
uv run --env-file .env python -m src.run_forecast \
  --run-time "$(date -u -d '2 hours ago' +%Y%m%dT%H00)"
```

Run labels represent **UTC**. For example, 18:00 UTC is 20:00 Berlin-local
time during daylight saving. Very recent runs may be incomplete; old runs may
have been removed from DWD. The command reports missing source fields and a
representative upstream URL before starting a failing Dagster materialization.

Existing validated local GRIB files can still be reprocessed after upstream
removal. If all four fields are already retained, the command does not contact
DWD for that partition.

## Dagster monitoring and automation

Use the same `DAGSTER_HOME` in every shell:

```bash
export DAGSTER_HOME="$PWD/.dagster_home"
```

List registered definitions:

```bash
uv run --env-file .env dg list defs
```

Start Dagster:

```bash
uv run --env-file .env dagster dev \
  -m src.dagster_pipeline.definitions
```

Open the local URL shown by Dagster. For automatic ingestion, find
`dwd_icon_d2_ruc_availability_sensor` under Automation and switch it on.

Sensor behavior:

- Stopped by default; starting the webserver does not enable it.
- Polls during minutes `:30`–`:59` of each hour.
- Requires all four forecast fields for the same run and lead time.
- Skips partitions whose final temperature/population contract is already valid.
- Preserves forecast run history and asset lineage under `DAGSTER_HOME`.

The manual forecast runner does not require the sensor to be started.

## Inspect the serving layer

Load the project environment into the current trusted shell when using the
database user/name variables directly:

```bash
set -a
source .env
set +a
```

Check the current partition and population-quality accounting:

```bash
docker compose --env-file .env -f docker/postgres.yml \
  exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT
      run_time_utc,
      lead_time,
      COUNT(*) AS plr_count,
      COUNT(*) FILTER (
        WHERE population_status = 'available'
      ) AS accepted_population,
      COUNT(*) FILTER (
        WHERE population_status = 'rejected_source_record'
      ) AS rejected_population
    FROM analytical.current_plr_weather_context
    GROUP BY run_time_utc, lead_time;
  "
```

Expected: one partition, 542 PLRs, 540 available population records, and two
explicitly rejected records.

Inspect values:

```bash
docker compose --env-file .env -f docker/postgres.yml \
  exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT
      plr_id,
      plr_name,
      valid_time_utc,
      valid_time_berlin,
      temperature_c,
      apparent_temperature_shade_c,
      plr_apparent_temperature_median_c,
      berlin_apparent_temperature_median_c,
      population_65plus,
      population_status
    FROM analytical.current_plr_weather_context
    ORDER BY plr_id
    LIMIT 10;
  "
```

Validate installed historical references without requiring historical source
files:

```bash
uv run --env-file .env python -m src.hostrada_snapshot validate
```

## Static-source fallback maintenance

Create an optional archive from an existing installation's retained static
source files:

```bash
uv run --env-file .env python -m src.static_snapshot create \
  --output /path/to/static-inputs.tar.xz
```

Inspect its internal source manifest:

```bash
uv run --env-file .env python -m src.static_snapshot inspect \
  --archive /path/to/static-inputs.tar.xz
```

Restore one source manually if necessary:

```bash
uv run --env-file .env python -m src.static_snapshot restore \
  --archive /path/to/static-inputs.tar.xz \
  --source lor_plr
```

The restore operation verifies the selected file and refuses to overwrite an
existing conflicting file.

## Forecast raw-file retention

Inspect what the retention utility would remove:

```bash
uv run --env-file .env python -m src.retention.weather_raw
```

The default retention period is seven days. Actual deletion requires an
explicit `--apply`:

```bash
uv run --env-file .env python -m src.retention.weather_raw --apply
```

Set `WEATHER_RAW_RETENTION_DAYS` or `WEATHER_RAW_PINNED_PARTITIONS` in `.env`
when a different policy is needed. Deleted GRIB files cannot be reprocessed
once the same upstream partition has disappeared.

## Database lifecycle

Stop the container while preserving the volume:

```bash
docker compose --env-file .env -f docker/postgres.yml stop
```

Restart it:

```bash
docker compose --env-file .env -f docker/postgres.yml up -d
```

`scripts/bootstrap_database.sh` is the internal, idempotent schema operation
used by `python -m src.bootstrap`; it is not a separate required installation
step. The two named SQL files under `sql/` are the complete supported database
initialization surface.

### Destructive reset

`scripts/reset_database.sh` exists as an explicit convenience for local
development. It **deletes the current Compose project's PostgreSQL volume and
all database state**, including imported references and forecast results.

Only run it after confirming that `.env` selects the intended Compose project:

```bash
bash scripts/reset_database.sh
```

The script requires the confirmation text `RESET`. Afterward, rerun the full
operational bootstrap, not the historical backfill.

## Isolated second installation

To run a clean-room installation beside an existing database, set different
values in the second checkout's `.env`:

```dotenv
POSTGRES_PORT=55432
POSTGRES_CONTAINER_NAME=capstone_cleanroom_postgres
COMPOSE_PROJECT_NAME=capstone_cleanroom
```

Set `DAGSTER_HOME` to the second checkout's own directory. Docker volumes,
containers, exposed ports, and Dagster run history remain independent.

## Failure handling

| Symptom | Meaning | Action |
| --- | --- | --- |
| Missing `.env` or required PostgreSQL variable | Local configuration is incomplete. | Copy `.env.example`, fill all required values, and retry. |
| Reference checksum or archive-size mismatch | Archive does not match the committed manifest. | Obtain the correct archive; do not bypass verification. |
| PLR geography fingerprint mismatch | Installed LOR geography differs from the reference snapshot. | Restore the matching geography or rebuild the reference deliberately. |
| Static source unavailable | An official endpoint or network connection is unavailable. | Supply `--static-snapshot`; use `--offline` when appropriate. |
| Population direct download fails | The provider's opaque direct URL may have changed. | The downloader restores the verified bundled CSV automatically; see [docs/data-sources.md](docs/data-sources.md). |
| Requested forecast run unavailable | Run is unpublished, incomplete, outside retention, or entered in local time. | Choose another UTC run; inspect missing fields shown by the runner. |
| Sensor shows no runs | The sensor is stopped or outside its polling window. | Enable it explicitly or use the manual forecast runner. |
| Existing schema rejected | The database is partial or incompatible. | Inspect the target; do not reset the wrong volume. |

For test selection, see [docs/testing.md](docs/testing.md). For source
reconstruction, see [docs/historical-rebuild.md](docs/historical-rebuild.md).
