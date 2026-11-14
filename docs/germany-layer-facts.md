# Germany layer: measured facts

Only values measured on this project's data and runs; nothing here is
estimated. Regenerated on 14 Nov 2026 from the installed database
(PostgreSQL 16.9, PostGIS 3.5.2, reached from the sandbox), the raw files and
the test runs of that day. The current forecast run is 2026-11-14 09:00 UTC.
Earlier measurements are kept only where a decision rests on them; they are
in the last section, with their dates.

Sizes in MB are PostgreSQL's `pg_size_pretty` units (1 MB = 1,048,576 bytes);
file sizes are given in bytes.

## Sources

| Fact | Value |
| --- | ---: |
| VG250 vintage | 31.12.2022 |
| VG250 GeoPackage archive (`vg250_12-31.utm32s.gpkg.ebenen.zip`) | 72,214,199 bytes |
| VG250 delivered features, Staat / Länder / Kreise / Gemeinden | 11 / 35 / 430 / 11,119 |
| of which land parts (GF = 4) | 1 / 16 / 400 / 10,990 |
| Zensus 2022 table | 1000A-1003 |
| Zensus raw rows, Länder / Kreise / Gemeinden extract (`GEOBL1` / `GEOLK4` / `GEOGM4`) | 204 / 4,812 / 129,444 |
| Zensus Gemeinden with a record | 10,786 |

## Normalized model (geography version `vg250-2022-12-31`)

| Fact | Value |
| --- | ---: |
| Länder / Kreise / Gemeinden | 16 / 400 / 10,990 |
| Gemeinden with a census value | 10,786 |
| Unincorporated areas (*gemeindefreie Gebiete*) | 206 |
| of which without a census record (population NULL) | 204 |
| of which with census residents | 2 (Lohheide 747, Osterheide 2,042) |
| Total Gemeinde area, EPSG:3035 | 357,201 km² |
| Invalid geometries, Länder / Kreise / Gemeinden | 0 / 0 / 0 |
| Largest relative difference, Σ Gemeinde area vs. Kreis area | 5.0 × 10⁻¹⁵ |

## Checks (run 14 Nov 2026 on the installed data)

All seven asset checks pass. Check 7 is defined with warning severity.

| Check | Result |
| --- | --- |
| 1 Key coverage | pass; 204 non-blocking exceptions (unincorporated areas without a census record), 0 blocking |
| 2 Hierarchy integrity | pass; 0 violations; 400 Kreise checked for the area partition, largest relative difference 5.0 × 10⁻¹⁵ (tolerance 10⁻⁹) |
| 3 Source totals | pass; 400 of 400 Kreise and 16 of 16 Länder exact; Σ Gemeinden 82,711,282 against the published Germany total 82,719,540, difference 8,258 = expected (consistent with staff abroad counted at Bund level only; the number is not published) |
| 4 Bridge coverage | pass; 10,990 Gemeinden, coverage 0.9999999999998317 … 1.0000000000001266, 0 exceptions (threshold 0.999) |
| 5 Forecast completeness | pass; runs 2026-11-09 19:00 and 2026-11-14 09:00 UTC each 25 of 25 lead times, 274,750 of 274,750 rows |
| 6 Plausibility | pass; 0 NULL and 0 out-of-bounds rows among 274,750 Gemeinde and 2,059,525 cell rows; observed −4.42 … 11.69 °C (temperature), −8.73 … 9.41 °C (apparent) |
| 7 Berlin cross-layer, run 2026-11-14 09:00 UTC | pass; 25 lead times; max difference 0.00090 °C (temperature), 0.00086 °C (apparent); tolerance 0.01 °C |
| 7 Berlin cross-layer, run 2026-11-09 19:00 UTC (re-run 14 Nov) | 0.00067 °C (temperature), 0.00096 °C (apparent) |

## Grid subset and bridge (ICON grid `icon_grid_0047_R19B07_L`)

| Fact | Value |
| --- | ---: |
| ICON cells intersecting a Gemeinde | 82,381 |
| Mean cell area, EPSG:3035 | 4.44 km² |
| Cell-Gemeinde bridge rows | 184,464 |
| Gemeinden in the bridge | 10,990 |
| Berlin bridge cells missing from the Germany subset | 0 |
| Size `de_icon_cell` (heap + indexes) | 48 MB |
| Size `de_icon_cell_gemeinde_bridge` (heap + indexes, incl. the foreign-key index) | 79 MB |

Comparison of the cells and bridge rebuilt with `ST_Transform` (14 Nov) with
the earlier ones built from vertices: identical row sets; largest absolute
difference of `fraction_of_gemeinde` 4.05 × 10⁻¹²; largest relative
difference of cell area 4.85 × 10⁻¹²; relative difference of intersection
area up to 1.58 × 10⁻⁷, only for slivers (0.0026 m²), at most 5.7 × 10⁻¹⁰ for
intersections of 1,000 m² or more.

## Forecast runs and storage

Retained runs: 2026-11-09 19:00 UTC and 2026-11-14 09:00 UTC (both complete);
current run 2026-11-14 09:00 UTC.

| Fact | Value |
| --- | ---: |
| Cell rows per run (82,381 cells × 25 lead hours) | 2,059,525 |
| Gemeinde rows per run (10,990 × 25) | 274,750 |
| Serving rows, current run: Gemeinde / Kreis / Land / Bund | 274,750 / 10,000 / 400 / 25 |
| DWD GRIB files, run 2026-11-14 09:00 UTC (4 fields × 25 lead times) | 82,102,546 bytes; 3,284,102 bytes per lead time on average |
| `de_icon_cell_value`, 2 runs (heap + indexes) | 588 MB (355 + 233) |
| `de_gemeinde_forecast`, 2 runs (heap + indexes) | 194 MB (105.7 + 88.5) |

## Values of the current run (2026-11-14 09:00 UTC)

| Fact | Value |
| --- | ---: |
| Germany, lead hour 0, air temperature, area- vs. population-weighted | 2.81 °C vs. 2.83 °C |
| Largest Kreis difference between the two weightings, apparent temperature | 2.35 °C (Reutlingen, lead hour 5) |
| Gemeinde 24-hour maxima of apparent temperature, range | −2.08 … 9.41 °C |
| Gemeinde 24-hour minima of apparent temperature, range | −8.73 … 0.06 °C |
| Germany, apparent temperature, area-weighted: 24-hour maximum / minimum | 2.93 °C (14 Nov 14:00) / −3.09 °C (15 Nov 05:00), Berlin time |
| Germany, apparent temperature, population-weighted: 24-hour maximum / minimum | 3.46 °C / −2.69 °C |

## Runtimes (14 Nov 2026, sandbox → host PostgreSQL)

| Step | Duration |
| --- | ---: |
| Static job `python -m src.germany.run_static` (6 checks pass, counts unchanged) | 143.4 s |
| `refresh_de_icon_bridge` (ST_Transform of cells + bridge) on an installed database | 126 s |
| Same without the foreign-key index on the bridge | 648 s |
| Forecast run 2026-11-14 09:00 UTC, 25 partitions incl. DWD download and decoding | 141.5 s (5.7 s per partition) |
| Publish (checks 5–7, retention) | 35.8 s |

## Map export (run 2026-11-14 09:00 UTC)

| Fact | Value |
| --- | ---: |
| Boundary tiles `vg250-2022-12-31.pmtiles` (3 layers, zoom 4–10, tippecanoe v2.53.0) | 13,139,513 bytes |
| `values.json` (schema 2, maxima and minima; 10,990 + 400 + 16 regions) | 5,259,646 bytes; 395,044 gzip-compressed |
| Same before the minima were added | 3,511,495 bytes; 281,537 gzip-compressed |
| Export of values only (tiles exist) | 26.2 s |
| Export with tile rebuild (before the minima were added) | 41.2 s |
| Scripted session in headless Chromium (load at Kreise, Gemeinden, three zoom steps, Länder, Tiefstwerte), requests to `data/` | 19 (values.json + 18 tile ranges), 8,382,022 bytes uncompressed, of which values.json 5,259,646; largest tile range 484,160 bytes |
| Console and page errors (1400 × 900 and 390 × 844; level and max/min switches, details panel) | 0 |
| Horizontal overflow at 390 px | none |
| HTTP Range on www.ohexel.com and ohexel.com (232 MB file), tested 2026-11-09 | 206 for first, middle and suffix ranges |

## Tests (14 Nov 2026)

| Suite | Result |
| --- | ---: |
| Default suite `uv run python -m pytest` | 171 passed, 53 deselected |
| Germany test cases (5 files, 45 test functions) | 69: 43 in the default suite, 5 integration, 21 acceptance |
| Germany integration tests (`-m integration`) | 5 passed in 19.1 s |
| Germany acceptance tests on the installed data | 21 passed in 230.2 s |
| `ruff check` | all checks passed |

Diff against `main` outside the Germany paths: 5 files added, 46 modified;
783 lines added, 127 removed. The removed lines are the unused
`from __future__ import annotations` and the blank line after it in 38 modules
(76 lines), and 51 lines of `uv.lock`, re-resolved when the ruff dev
dependency was added (lock revision 3 to 5; it drops an orphaned `pyarrow`
entry that no package depends on).

## Earlier measurements that decisions rest on

### SQL vs. Python aggregation

Measured 2026-11-09; basis for ADR 018. Both implementations ran on the same decoded fields: 102 partitions of four
runs (2026-11-09 16:00, 17:00, 18:00 and 19:00 UTC; lead 0 of the first run
measured three times). The runs 16:00–18:00 used the earlier wide cell table,
the run 19:00 the current slim layout. Raw rows: 714, exported to
`de_aggregation_benchmark-2026-11-09.csv` outside the repository before the
benchmark table was dropped. Seconds per partition:

| Stage | Implementation | Runs 16–18 (77 partitions): mean / median / max | Run 19 (25 partitions): mean / median / max |
| --- | --- | ---: | ---: |
| Load bridge from PostgreSQL | shared | 0.563 / 0.557 / 0.814 | 0.606 / 0.567 / 1.032 |
| Decode 4 GRIB fields, select Germany cells | shared | 0.125 / 0.120 / 0.605 | 0.127 / 0.124 / 0.158 |
| COPY 4 raw indicators of 82,381 cells | SQL | 0.946 / 0.942 / 1.166 | 0.953 / 0.952 / 1.065 |
| Units, apparent temperature, store cells, aggregate, store Gemeinden | SQL | 1.038 / 1.069 / 1.803 | 1.259 / 1.149 / 1.816 |
| Units, apparent temperature, aggregate (numpy) | Python | 0.007 / 0.007 / 0.022 | 0.008 / 0.008 / 0.013 |
| COPY 10,990 Gemeinde values | Python | 0.079 / 0.075 / 0.186 | 0.081 / 0.079 / 0.121 |
| COPY 82,381 cell values | Python | 0.477 / 0.474 / 0.580 | 0.493 / 0.489 / 0.556 |
| **Per partition, aggregation and storage** | SQL / Python | 1.98 / 0.56 | 2.21 / 0.58 |

Per run of 25 lead hours (one measurement per lead hour, without loading the
bridge): SQL 52.7, 48.1 and 47.2 s for the runs 16–18 and 55.3 s for the run
19; Python 14.3, 13.8, 14.1 and 14.5 s. Largest difference between the two
results over all 102 partitions: 1.4 × 10⁻¹⁴ °C (floating-point summation
order).

### Cell storage layouts

Measured 2026-11-09; basis for ADR 018.

| Layout, 2 retained runs | Size (heap + indexes) |
| --- | ---: |
| First layout `de_icon_cell_weather` (run, lead time and grid keys on every row) | 1,259 MB (743 + 516) |
| Slim layout `de_icon_cell_value`, freshly written | 361 MB (237 + 124) |
| Slim layout with `real` (float4) instead of `double precision` (not adopted) | 329 MB (205 + 124) |

The current 588 MB (above) is the same slim layout after retention cycles; on
9 Nov, after one cycle, it was 541 MB, because the space of a pruned run is
reused by PostgreSQL, not returned to the operating system.

### Weighting example

Measured 2026-11-09; cited in ADR 015. Germany, lead hour 0 of the run 2026-11-09 16:00 UTC (no longer retained),
air temperature: 5.25 °C area-weighted against 6.08 °C population-weighted.
