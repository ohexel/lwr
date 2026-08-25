# ADR 003: Forecast partitions reflect DWD publication and retention

Status: Accepted.

## Context

DWD publishes ICON-D2-RUC fields on a rolling schedule and retains each run for
a limited period. A valid-looking run label therefore does not guarantee that
all required source fields are currently downloadable.

## Decision

Identify a forecast partition by its UTC model run and supported lead time.
Check publication of all required forecast fields before starting a new manual
run, and explain incomplete, future, unpublished, or expired partitions in
operator-facing errors.

Skip upstream availability checks for fields already retained locally so
historical reprocessing remains possible after DWD removes the remote run.
Keep the Dagster polling sensor stopped by default and require an explicit
operator action to enable automatic ingestion.

## Consequences

Manual demonstrations do not depend on the sensor polling window. Users must
distinguish UTC run labels from Berlin-local display times and choose a
published run. Local raw-file retention, not DWD availability, determines how
long an already downloaded partition can be reprocessed without reacquisition.
