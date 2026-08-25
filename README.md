# Berlin neighborhood temperature context

A reproducible geospatial data pipeline that combines current German Weather
Service temperature forecasts, 31 years of historical temperature, and official
population data for Berlin's 542 planning areas.

The final PostgreSQL view gives analysts neighborhood-level temperature and
shade apparent temperature, comparable neighborhood and citywide historical
reference values, and the number of residents aged 65 or older. It does **not**
label heat risk, infer individual exposure, or silently replace rejected
population records with zero.

The engineering contribution is a resource-conscious path from heterogeneous
GeoJSON/WFS, CSV, GRIB2, and NetCDF sources to one stable analytical contract.
It reconciles nationwide model grids with neighborhood polygons, UTC forecast
partitions with Berlin-local historical calendar hours, and slowly changing
population data with hourly temperature values. The order of operations is
deliberate: filter nationwide forecasts to Berlin before loading them, build
reusable area-weighted spatial bridges once, process large historical files one
month at a time, and distribute the resulting reference statistics as a
verified 232 MB snapshot instead of making every user repeat a roughly 220 GB
historical download.

| Challenge | Engineering solution | Result |
| --- | --- | --- |
| Heterogeneous source formats and grains | Source-specific acquisition and decoding, then explicit PostgreSQL contracts | One traceable PLR-level serving grain |
| Different forecast, historical, and administrative geometries | Versioned PostGIS intersection bridges with area weighting | Comparable neighborhood values without pretending the source grids match |
| Large nationwide and multi-decade inputs | Filter early, join on reusable bridges, aggregate monthly, and delete validated monthly source files | Bounded storage and restartable processing |
| Expensive historical reconstruction | Export only validated PLR and Berlin calendar-hour statistics | A clean installation in about ten minutes rather than a full HOSTRADA rebuild |

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

## Process and inspect a forecast

Forecast run labels are **UTC**, not Berlin-local time. Recent runs may not yet
be published and older runs may have left DWD's rolling availability window.

On GNU/Linux, this selects a run from approximately two hours ago:

```bash
uv run --env-file .env python -m src.run_forecast \
  --run-time "$(date -u -d '2 hours ago' +%Y%m%dT%H00)"
```

The default lead time is `PT000H00M`; supported alternatives are displayed by:

```bash
uv run --env-file .env python -m src.run_forecast --help
```

Query the final serving view using the default local database credentials:

```bash
docker compose --env-file .env -f docker/postgres.yml \
  exec -T postgres psql -U capstone -d capstone -c "
    SELECT
      plr_id,
      valid_time_utc,
      temperature_c,
      apparent_temperature_shade_c,
      plr_temperature_median_c,
      berlin_temperature_median_c,
      population_65plus,
      population_status
    FROM analytical.current_plr_weather_context
    ORDER BY valid_time_utc, plr_id
    LIMIT 10;
  "
```

If you changed the database name or user in `.env`, substitute those values in
the command above. A complete partition contains one row for each of 542 PLRs;
the two rejected population records remain visible with their explicit status.

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
