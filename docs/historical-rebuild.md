# Optional HOSTRADA historical reconstruction

The operational installation imports a precomputed historical reference and
does not require this workflow. Reconstruct the reference only when verifying
the original processing, changing the reference contract, or replacing the
reference geography or period.

An existing operational installation is the simplest starting point: its
canonical schema, current PLR geography, and normal application environment are
already present. Historical reconstruction then adds its own HOSTRADA grid,
spatial bridge, monthly observations, and source manifests.

## Cost and prerequisites

- 31 reference years: January 1995 through December 2025.
- 372 monthly partitions and 1,116 source files.
- Approximately 220 GB of source downloads across the complete run.
- 271,752 Berlin-hour observations and 147,289,584 PLR-hour observations
  before calendar filtering.
- Substantial additional PostgreSQL storage and a long-running download and
  aggregation process; duration depends on network, CPU, disk, and database
  configuration.
- Persistent `DAGSTER_HOME`, working PostgreSQL/PostGIS, and the installed
  project Python environment.

Do not run this workflow merely to start the project, and do not point a
destructive reset at an operational database that must be preserved. A
separate PostgreSQL volume is appropriate when reconstruction should remain
isolated from normal forecasting.

## 1. Initialize historical spatial prerequisites

Start Dagster from the initialized project:

```bash
uv run --env-file .env dagster dev \
  -m src.dagster_pipeline.definitions
```

In the Dagster interface, find and execute the `hostrada_spatial` job. This
materializes the historical HOSTRADA grid and its own area-weighted Berlin PLR
bridge. The historical grid is distinct from the operational ICON forecast
grid; both share the installed versioned PLR geography.

## 2. Backfill historical monthly observations

Pilot one month before committing to the full archive:

```bash
uv run --env-file .env python -m src.hostrada_backfill \
  --start 1995-01 \
  --end 1995-01
```

If the pilot passes, run the full interval:

```bash
uv run --env-file .env python -m src.hostrada_backfill \
  --start 1995-01 \
  --end 2025-12
```

The runner processes independent UTC calendar months. Each complete monthly
partition is quality-checked before its original NetCDF files are removed;
source manifests preserve their provenance and deletion status. Restarting the
same command skips partitions that already satisfy their quality contract.

The existing `sql/bootstrap_schema.sql` already contains every relation and
function required for this optional workflow.

## 3. Validate the completed backfill

Expected totals:

| Measure | Expected |
| --- | ---: |
| UTC calendar months | 372 |
| Source manifests | 1,116 |
| Source files recorded as deleted | 1,116 |
| Berlin historical hourly rows | 271,752 |
| PLR historical hourly rows | 147,289,584 |

For any month, the Berlin hourly count must equal its UTC-hour count, and the
PLR hourly count must equal the UTC-hour count multiplied by 542. Existing
monthly quality checks provide the authoritative acceptance gate.

## 4. Rebuild the local-calendar reference

Pilot February, the smallest reference partition:

```bash
uv run --env-file .env python -m src.hostrada_reference_build \
  --month 02 \
  --force
```

Rebuild all twelve calendar months:

```bash
uv run --env-file .env python -m src.hostrada_reference_build --force
```

`--force` is important when starting from an installation that already imported
a valid reference snapshot: without it, valid existing months may be skipped.
When resuming after an interrupted forced build, rerun individual failed months
with `--month MM --force` or rerun without `--force` once all desired
replacement months have been processed.

The reference uses Berlin-local month, day, and hour; excludes local February
29; preserves both UTC observations when autumn repeats a local hour; and does
not fabricate observations during the spring clock change.

Expected results:

| Measure | Expected |
| --- | ---: |
| Berlin reference rows | 8,760 |
| PLR reference rows | 4,747,920 |
| Historical Berlin observations entering reference aggregation | 271,559 |
| Historical PLR observations entering reference aggregation | 147,184,978 |
| Reference observations per local-calendar hour | 26–36 |
| Reference rows for February 29 | 0 |

## 5. Publish a replacement operational snapshot

Export only the two serving-layer reference tables after all quality checks
pass:

```bash
docker compose --env-file .env -f docker/postgres.yml exec -T postgres \
  pg_dump \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --format=custom \
    --compress=6 \
    --data-only \
    --no-owner \
    --no-privileges \
    --table=analytical.hostrada_plr_hourly_reference \
    --table=analytical.hostrada_berlin_hourly_reference \
  > hostrada-reference-1995-2025.pgcustom
```

Ensure `POSTGRES_USER` and `POSTGRES_DB` are exported in the shell first, or
replace those arguments with their documented project values. Regenerating the
archive changes its size and SHA-256 even when the logical rows are equivalent.
Update the committed HOSTRADA manifest, reference metadata, and distribution
instructions together before publishing the new artifact.

For routine installation and artifact validation, return to
[../runbook.md](../runbook.md) and [distribution.md](distribution.md).
