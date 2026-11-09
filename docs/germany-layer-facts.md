# Germany layer: measured facts

Only values measured on this project's data and runs. Dates are measurement
dates. Nothing here is estimated.

## Sources (measured 2026-11-09)

| Fact | Value |
| --- | ---: |
| VG250 vintage | 31.12.2022 |
| VG250 GeoPackage archive | 72,214,199 bytes |
| VG250 delivered features (Staat / Länder / Kreise / Gemeinden) | 11 / 35 / 430 / 11,119 |
| Zensus 2022 table | 1000A-1003 |
| Zensus raw rows loaded (Länder / Kreise / Gemeinden extracts) | 204 / 4,812 / 129,444 |
| Zensus Gemeinden with a record | 10,786 |

## Normalized model (geography version `vg250-2022-12-31`)

| Fact | Value |
| --- | ---: |
| Länder | 16 |
| Kreise | 400 |
| Gemeinden (VG250 land parts, GF = 4) | 10,990 |
| of which unincorporated areas (*gemeindefreie Gebiete*) | 206 |
| unincorporated areas without a census record (population NULL) | 204 |
| unincorporated areas with census residents (Lohheide, Osterheide) | 2 |
| Total Gemeinde area, EPSG:3035 | 357,201 km² |
| Invalid geometries after transformation | 0 |
| Max. relative difference, Σ Gemeinde area vs. Kreis area | 5.0 × 10⁻¹⁵ |

## Source checks

| Check | Result |
| --- | --- |
| 4 Bridge coverage | pass; Gemeinde coverage 0.99999999999977 … 1.00000000000013, 0 exceptions |
| 5 Forecast completeness | pass for runs 2026-11-09 16:00, 17:00, 18:00, 19:00 UTC |
| 6 Plausibility | pass for the same runs |
| 7 Berlin cross-layer | pass; max difference 0.0014 °C over 75 lead times of runs 16–18 UTC; run 19:00 UTC: 0.0007 °C (temperature), 0.0010 °C (apparent) (tolerance 0.01 °C) |
| 1 Key coverage | pass; 204 non-blocking exceptions (unincorporated areas), 0 blocking |
| 2 Hierarchy integrity | pass; 0 violations |
| Σ Gemeinde population per Kreis = published Kreis total | 400 of 400 Kreise exact |
| Σ Gemeinden = Σ Kreise = Σ Länder | 82,711,282 |
| Published Germany total | 82,719,540 (+8,258 vs. Σ Länder; consistent with staff abroad counted at Bund level only, number not published) |
| 3 Source totals | pass; 400 of 400 Kreise and 16 of 16 Länder exact, Bund difference 8,258 = expected |

## Grid subset and bridge (ICON grid `icon_grid_0047_R19B07_L`)

| Fact | Value |
| --- | ---: |
| ICON cells intersecting a Gemeinde | 82,381 |
| Mean cell area | 4.45 km² |
| Cell-Gemeinde bridge rows | 184,464 |
| Gemeinden in the bridge | 10,990 |
| Berlin bridge cells missing from the Germany subset | 0 |
| `refresh_de_icon_bridge` runtime | 102.7 s |
| Size: `de_icon_cell` / `de_icon_cell_gemeinde_bridge` | 24 MB / 42 MB |

## Forecast runs (measured 2026-11-09)

| Fact | Value |
| --- | ---: |
| Rows per run: cells × 25 lead times | 2,059,525 |
| Rows per run: Gemeinden × 25 lead times | 274,750 |
| Serving rows per run: Kreis / Land / Bund | 10,000 / 400 / 25 |
| Wall time of one 25-lead run incl. DWD download, both implementations | 156 s, 167 s, 166 s |
| Same, slim cell layout (run 19:00 UTC) | 237 s (214 s partitions + 23 s publish) |
| SQL compute-and-store per lead time, slim layout (run 19:00 UTC, mean) | 1.26 s (wide layout: 0.94–1.04 s) |
| DWD GRIB download per lead time (4 fields) | 3.3 MB |
| Storage with 2 retained runs, first layout `de_icon_cell_weather` (heap + indexes) | 1,259 MB (743 + 516) |
| Same 2 runs in the slim layout `de_icon_cell_value`, freshly written (heap + PK) | 361 MB (237 + 124) |
| Same 2 runs, slim layout with float4 instead of double precision (not adopted) | 329 MB (205 + 124) |
| Slim layout on disk after one retention cycle (space of the pruned run is reused, not returned) | 541 MB (355 + 186) |
| Storage with 2 retained runs: `de_gemeinde_forecast` | 168 MB |
| Bund mean at lead 0 of run 16:00 UTC, area- vs. population-weighted temperature | 5.25 °C vs. 6.08 °C |

## SQL vs. Python aggregation

77 partitions (3 runs, lead 0 of the first run measured three times), same
decoded fields. Seconds per partition:

| Stage | Implementation | Mean | Median | Max |
| --- | --- | ---: | ---: | ---: |
| Load bridge from PostgreSQL | shared | 0.563 | 0.557 | 0.814 |
| Decode 4 GRIB fields, select Germany cells | shared | 0.125 | 0.120 | 0.605 |
| COPY 4 raw indicators of 82,381 cells | SQL | 0.946 | 0.942 | 1.166 |
| Units, apparent temperature, store cells, aggregate, store Gemeinden | SQL | 1.038 | 1.069 | 1.803 |
| Units, apparent temperature, aggregate (numpy) | Python | 0.007 | 0.007 | 0.022 |
| COPY 10,990 Gemeinde values | Python | 0.079 | 0.075 | 0.186 |
| COPY 82,381 cell values | Python | 0.477 | 0.474 | 0.580 |

Per run (25 partitions, without bridge loading): SQL 47.2–57.5 s,
Python 13.8–15.4 s including cell storage. Largest difference between the two
results: 1.4 × 10⁻¹⁴ °C (floating-point summation order).

## Runtimes (sandbox → host PostgreSQL 16.9 / PostGIS 3.5.2)

| Step | Duration |
| --- | ---: |
| Raw load, VG250 (4 levels) + Zensus (3 extracts) | 10.4 s wall |
| `refresh_de_hierarchy` | 11.4 s |
| `refresh_de_population` | 1.0 s |

## Tests (2026-11-09, after M4)

| Suite | Result |
| --- | ---: |
| Default suite `uv run python -m pytest` (Berlin + Germany unit and contract) | 152 passed |
| Germany tests incl. integration and acceptance (4 files) | 36 passed |
| Germany acceptance tests on the installed database (run 19:00 UTC served) | 6 passed in 44 s |
| Diff vs. `main` (lines removed from existing files) | 0 |
