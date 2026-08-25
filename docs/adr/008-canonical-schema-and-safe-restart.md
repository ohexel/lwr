# ADR 008: Initialize from one canonical schema and restart safely

Status: Accepted.

## Context

A new user needs one unambiguous database entry point. Ordinary startup must
also distinguish an empty database, a complete compatible installation, and a
partially initialized database without risking existing data.

## Decision

Use `sql/bootstrap_schema.sql` as the authoritative fresh-database schema and
two descriptively named additive compatibility files for already initialized
databases: `sql/hostrada_reference_snapshot_validation.sql` and
`sql/plr_display_names.sql`. Fresh installations require only the canonical
schema; compatibility upgrades preserve existing data.

Make operational initialization safe to repeat: verify the archive before
expensive work, preserve existing schema and data, reuse complete static
materializations, and skip already valid reference snapshots. Keep database
reset in a separate convenience script that requires explicit confirmation.

## Consequences

New users can find the supported database entry point immediately. A completed
installation can be rerun without modifying valid data. A partial or
incompatible schema stops with an explicit error. Intentional deletion remains
possible, but it is visibly separated from ordinary startup and requires
typing `RESET`.
