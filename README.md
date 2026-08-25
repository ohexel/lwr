# Berlin neighborhood temperature context

A reproducible geospatial data pipeline that combines current DWD temperature
forecasts, 31 years of historical temperature, and official population data for
Berlin's 542 planning areas.

The final PostgreSQL view gives analysts named neighborhoods, neighborhood-level
temperature and shade apparent temperature, comparable neighborhood and
citywide historical reference values, and the number of residents aged 65 or
older.

- heterogeneous source formats and grains transformed into one traceable PLR-level serving grain
- different forecast, historical, and administrative geometries are reconciled using versioned PostGIS intersection bridges with area weighting
- condensing large nationwide and multi-decade inputs: filter early, join on bridges, aggregate monthly, and delete validated monthly source files
- replicable and quick startup: clean installation in ten minutes thanks to validated checksum-verified artefacts (instead of several-hours full historical rebuild)

**The pipeline at a glance:**  
- Partitioned batch pipeline (multi-dimensional: run_time × lead_time)
- persistent source data in `raw`, 3NF tables in `normalized`, one fully denormalized OBT view served to analysts
- Dagster software-defined assets with asset lineage, asset checks as data-quality gates, and a sensor-based trigger
- PostGIS geospatial ETL:area-weighted spatial joins, reusable intersection bridge tables, CRS reprojection
- SQL-based data contracts plus pytest "contract" tests
- checksum-verified, manifest-driven snapshot distribution for the historical reference (~220MB analysis-ready artefact instead of reprocessing 220GB source data)

## Run a forecast and inspect the result

After completing the [one-time installation and bootstrap](#install-and-initialize),
run these two commands from the project root. On GNU/Linux, the first processes
a forecast from approximately two hours ago:

```bash
uv run --env-file .env python -m src.run_forecast \
  --run-time "$(date -u -d '2 hours ago' +%Y%m%dT%H00)"
```

The second queries the final analytical view using the PostgreSQL credentials
already configured inside the database container:

```bash
docker compose --env-file .env -f docker/postgres.yml \
  exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT
      plr_id,
      plr_name,
      valid_time_berlin,
      ROUND(temperature_c::numeric, 1) AS temperature_c,
      ROUND(apparent_temperature_shade_c::numeric, 1)
        AS apparent_temperature_c,
      ROUND(plr_temperature_median_c::numeric, 1)
        AS historical_median_c,
      population_65plus
    FROM analytical.current_plr_weather_context
    ORDER BY plr_id
    LIMIT 10;
  "'
```

Example result:

| PLR ID | Neighborhood | Berlin-local time | Temperature °C | Apparent °C | Historical median °C | Residents 65+ |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `01100101` | Stülerstraße | 2026-08-24 18:00 | 20.8 | 19.5 | 22.6 | 700 |
| `01100102` | Großer Tiergarten | 2026-08-24 18:00 | 20.8 | 19.6 | 22.7 | 291 |
| `01100103` | Lützowstraße | 2026-08-24 18:00 | 20.8 | 19.5 | 22.7 | 1,106 |

Forecast run labels use UTC; very recent runs may not yet be published and
older runs may have left DWD's rolling availability window. A complete
partition contains all 542 planning areas. The Dagster UI is not required.

## Start here

| Document | Use it when you want to... |
| --- | --- |
| [architecture.md](architecture.md) | Understand data sources, layers, spatial joins, orchestration, and the serving contract. |
| [runbook.md](runbook.md) | Install, operate, inspect, troubleshoot, or reset the pipeline. |
| [docs/data-sources.md](docs/data-sources.md) | Check source contracts, acquisition behavior, licenses, and attribution. |
| [docs/distribution.md](docs/distribution.md) | Obtain, verify, or publish the external installation archives. |
| [docs/testing.md](docs/testing.md) | Choose the small set of tests appropriate to your environment. |
| [docs/historical-rebuild.md](docs/historical-rebuild.md) | Optionally reconstruct the HOSTRADA reference from original DWD data. |
| [docs/adr/README.md](docs/adr/README.md) | Review the principal architectural decisions and their tradeoffs. |

## What you need

- Linux or another environment that can run Docker and Docker Compose.
- Python 3.11, installed or managed by `uv`.
- `uv`, Docker Engine, and the Docker Compose plugin.
- Internet access to Python packages, the PostGIS image, Berlin Open Data, and
  the DWD Open Data server; an optional static snapshot can replace source
  downloads after dependencies and the container image are installed.
- The separately distributed HOSTRADA reference archive,
  `hostrada-reference-1995-2025.pgcustom`.
- Sufficient local disk space for PostgreSQL, the 201 MB compressed ICON grid,
  the 232 MB reference archive, and the resulting database tables and indexes.

Python dependencies are declared in `pyproject.toml`, locked in `uv.lock`, and
installed together with the `ecCodes`, PostGIS-client, and geospatial bindings
needed by the project. PostgreSQL and PostGIS run in Docker; application code
runs in the locked Python environment.

## Install and initialize

Clone the repository and enter its root:

```bash
git clone <repository-url> berlin-temperature-context
cd berlin-temperature-context
```

Create the local configuration and choose a PostgreSQL password:

```bash
cp .env.example .env
```

Install the exact locked Python environment:

```bash
uv sync --frozen
```

Start PostgreSQL/PostGIS:

```bash
docker compose --env-file .env -f docker/postgres.yml up -d
```

Use persistent project-local Dagster state:

```bash
export DAGSTER_HOME="$PWD/.dagster_home"
```

Initialize the complete operational pipeline:

```bash
uv run --env-file .env python -m src.bootstrap \
  --reference-archive /path/to/hostrada-reference-1995-2025.pgcustom
```

If you also have the optional static-source archive, provide it as a verified
fallback for unavailable geography, population, or ICON-grid downloads:

```bash
uv run --env-file .env python -m src.bootstrap \
  --reference-archive /path/to/hostrada-reference-1995-2025.pgcustom \
  --static-snapshot /path/to/static-inputs.tar.xz
```

Add `--offline` to use that snapshot directly instead of attempting static
source downloads. The initial clean-room bootstrap took approximately ten
minutes on the development laptop. Subsequent runs preserve existing data,
validate completed static inputs, and do not reimport an already valid
historical reference.

A successful bootstrap reports `"status": "ready"`. It does not download the
1995–2025 historical HOSTRADA archive.
The official 542-PLR name directory is acquired automatically; a verified,
64 KB workbook included in the repository supplies its offline fallback.

## Orchestration and tests

Inspect the registered jobs and assets:

```bash
uv run --env-file .env dg list defs
```

Start the Dagster interface:

```bash
uv run --env-file .env dagster dev \
  -m src.dagster_pipeline.definitions
```

The forecast availability sensor is intentionally **stopped by default**. Start
`dwd_icon_d2_ruc_availability_sensor` manually in the Dagster interface when
automated ingestion is desired. It checks whether all required forecast fields
are available before launching a run.

For an optional background interface on this branch, see
[containerized Dagster](docs/optional-dagster-container.md). The normal
host-based commands and PostgreSQL installation remain unchanged.

Run the default database-independent test suite:

```bash
uv run python -m pytest
```

After completing the operational bootstrap and processing at least one
forecast, run the end-to-end acceptance checks:

```bash
uv run --env-file .env python -m pytest \
  -m "acceptance and not historical_rebuild"
```

See [docs/testing.md](docs/testing.md) for focused data contracts, database
integration checks, and the optional historical-rebuild suite.

## What is distributed separately

| Artifact | Required? | Size | Purpose |
| --- | --- | ---: | --- |
| `hostrada-reference-1995-2025.pgcustom` | Yes | 231,645,982 bytes | Precomputed PLR and Berlin historical references. |
| `static-inputs.tar.xz` | Optional | 198,011,500 bytes | Verified fallback copies of LOR geography, population, and the ICON grid. |

Expected archive checksums and contents are recorded under `snapshots/`; see
[docs/distribution.md](docs/distribution.md). Neither archive belongs in Git.

## Scope and data interpretation

The pipeline provides **shade apparent temperature**, derived from air
temperature, relative humidity, and wind. It does not incorporate solar
radiation and therefore does not claim to calculate comprehensive heat stress.

The HOSTRADA reference uses the current 542-PLR geography, the years 1995–2025,
Berlin-local calendar hours, and median/p90/maximum values. Historical February
29 is excluded. Daylight-saving gaps and repeated hours are preserved according
to the `Europe/Berlin` timezone rules.

Geography-version identifiers, population reference dates, source provenance,
and quality diagnostics remain available below the serving layer. Analysts
receive measurements and essential time/geographic context rather than
predefined thresholds, classifications, or exposure estimates.
