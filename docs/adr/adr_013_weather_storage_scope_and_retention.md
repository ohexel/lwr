# ADR 013: Scope forecast storage to Berlin and limit retention to 24 hours

Status: Accepted.

## Context

Each ICON-D2-RUC forecast indicator contains 542,040 grid-cell values, while
the current Berlin forecast mask contains 465 relevant cells. The operational
pipeline requires four source indicators, so storing every decoded nationwide
value would write 2,168,160 source values per forecast partition despite the
product serving only Berlin planning areas.

Forecasts also become obsolete quickly. Retaining superseded runs for several
days increases local storage and makes Dagster suggest historical partitions
that are no longer useful to the operational product.

## Decision

Keep the complete compressed GRIB files only within the configured forecast
retention window. Decode and validate each complete source field, including
its expected grid size and missing-value accounting, but select the verified
Berlin-mask cell indices in Python before crossing into PostgreSQL.

PostgreSQL stores only:

- `raw.icon_d2_ruc_source`: one source manifest per run, lead, and indicator.
- `raw.icon_d2_ruc_field`: source-faithful values for the Berlin forecast mask.
- The normalized and analytical forecast rows derived from those values.
- Explicit forecast-quality rejection records when needed.

The versioned spatial mask is identified by:

```text
geography_version
source_grid_id
mask_buffer_m
```

`WEATHER_MASK_BUFFER_M` defaults to 5,000 meters. All four indicators remain
inside one replacement transaction; an invalid field, invalid mask index, or
failed partition-quality check prevents the forecast from being accepted.

## Retention contract

```text
FORECAST_RETENTION_HOURS=24
```

The same UTC model-run cutoff applies to retained GRIB files, acquisition
sidecars, source manifests, Berlin-scoped raw values, normalized forecast rows,
analytical forecast rows, and forecast rejection records. The default and
maximum are 24 hours; shorter whole-hour windows are allowed.

Each successful final forecast partition triggers cleanup. Expired forecast
database rows are removed in one transaction, with children deleted before
their parents. Expired source files and sidecars are removed afterward.

HOSTRADA historical observations, compact historical references, static PLR
geography, population records, and reusable spatial infrastructure are never
included in the forecast deletion allowlist.

Dagster derives its visible forecast-partition start when the code location
loads. Reloading the location advances the displayed window. The sensor,
manual runner, and database/file cleanup calculate their active cutoff at run
time.

## Consequences

The project preserves full-field source validation and provenance while
sending only Berlin-relevant values across the Python/PostgreSQL boundary.
Forecast data remains reprocessable without another DWD request only while
its source files remain inside the bounded local retention window.

Old forecast partitions cannot be pinned indefinitely. Deleting PostgreSQL
rows makes their space available for normal reuse, but does not guarantee an
immediate reduction in the database's physical file size.
