# ADR 001: Python, PostGIS, and Dagster have distinct responsibilities

Status: Accepted.

## Context

The pipeline ingests CSV, GeoJSON, GRIB, and NetCDF sources; performs spatial
and relational transformations; and orchestrates hourly forecasts and optional
historical processing. These concerns require different execution boundaries.

## Decision

- Python handles HTTP acquisition, source-format decoding, checksum validation,
  and application-level orchestration helpers.
- PostgreSQL/PostGIS owns durable relational state, geographic normalization,
  spatial intersections, area-weighted aggregation, quality functions, and
  serving views.
- Dagster coordinates asset dependencies, partition execution, sensors,
  retries, run visibility, and restartable historical processing.
- Docker isolates PostgreSQL/PostGIS; `uv.lock` pins the application environment.
- Database schemas are named `raw`, `normalized`, and `analytical`.

The project does not add Spark, dbt, or a streaming platform because the
observed data volume, hourly publication cadence, and spatial workloads do not
require another processing system.

## Consequences

The architecture remains inspectable with ordinary Python, SQL, and Dagster
tools. PostGIS behavior is tested against a real database instead of
reimplemented in application fixtures. Operators manage one durable database
and one persistent Dagster instance rather than coordinating several services.
