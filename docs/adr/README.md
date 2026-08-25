# Architecture decision records

These records describe the current supported project architecture. Read them in
order when reviewing why the pipeline, storage boundaries, reference snapshot,
and serving contract are shaped as they are.

1. [Processing and storage boundaries](001-python-postgis-dagster-boundaries.md)
2. [Current PLR geography and area-weighted spatial joins](002-current-plr-geography-and-area-weighting.md)
3. [Source-aware forecast partitions](003-source-aware-forecast-partitions.md)
4. [Population quality and explicit rejection](004-population-quality-and-explicit-rejection.md)
5. [Precomputed historical reference snapshot](005-precomputed-historical-reference-snapshot.md)
6. [Berlin-local historical calendar](006-berlin-local-historical-calendar.md)
7. [Lean, analyst-owned serving contract](007-lean-analyst-owned-serving-contract.md)
8. [Canonical schema and safe restart](008-canonical-schema-and-safe-restart.md)
9. [Berlin-scoped forecast storage and bounded retention](adr_013_weather_storage_scope_and_retention.md)

Earlier working notes or development-era ADRs may remain in this directory as
project detail. The records linked above describe the supported handoff state;
the authoritative operational commands are in [../../runbook.md](../../runbook.md).
