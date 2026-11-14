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
10. [Complete forecast horizon and median-based summary](009-complete-forecast-horizon-and-median-summary.md)
11. [Static, dependency-free temperature dashboard](010-static-temperature-dashboard.md)

## Germany layer (v2.0)

- ADR 014: [Equal-area projection for Germany-wide area weights](014-equal-area-projection-for-germany.md)
- ADR 015: [Roll-up weighting for Kreis, Land and Bund](015-rollup-weighting.md)
- ADR 016: [Germany map: PMTiles outlines and a JSON values file](016-map-pmtiles-and-json-values.md)
- ADR 017: [Zensus 2022 population and the checks against the source](017-zensus-2022-population-and-source-checks.md)
- ADR 018: [SQL aggregation of the forecast, and per-cell storage](018-sql-aggregation-and-cell-storage.md)

## Germany layer, second theme (v2.1)

- ADR 019: [A theme-independent indicator model, filled first with the BA Gemeindedaten](019-indicator-model-and-ba-gemeindedaten.md)

Earlier working notes or development-era ADRs may remain in this directory as
project detail. The records linked above describe the supported handoff state;
the authoritative operational commands are in [../../runbook.md](../../runbook.md).
