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

Skip upstream availability checks for fields already retained locally so a
forecast can be reprocessed within its configured local retention window after
DWD removes the remote run. Keep that window no longer than 24 hours, scope
visible Dagster partitions to recent runs, and reject older manual requests.
Keep the Dagster polling sensor stopped by default and require an explicit
operator action to enable automatic ingestion.

## Consequences

Manual demonstrations do not depend on the sensor polling window. Users must
distinguish UTC run labels from Berlin-local display times and choose a
published run. Local retention, not DWD availability, determines how long an
already downloaded partition can be reprocessed without reacquisition; the
project does not maintain a permanent archive of superseded forecast runs.
