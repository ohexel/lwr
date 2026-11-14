# ADR 018: SQL aggregation of the forecast, and per-cell storage

Status: Accepted on the `germany-layer` branch (14 Nov 2026). The storage
choice in the second part is a deliberate trade-off that may need to be
revisited if storage becomes an issue (see below).

## Context

For every forecast hour the pipeline turns four fields on the ICON-D2 grid
(temperature, relative humidity, two wind components) into a temperature and a
shade apparent temperature per grid cell, and then into an area-weighted value
per Gemeinde through the cell-Gemeinde bridge (ADR 014). This can run in
PostgreSQL, as the Berlin pipeline does, or in Python with a sparse weighted
sum (numpy). The handover plan said to measure one run first and then decide.

## Decision

### Aggregate in SQL

Both versions were run on the same decoded fields for 102 partitions of four
runs: three runs on the earlier wide cell table (77 partitions) and one on the
slim layout that is in use now (25 partitions). SQL stages the four indicators in a temporary table, then one SQL
function (`normalized.refresh_de_forecast_partition`) converts units, calls
Berlin's `normalized.calculate_apparent_temperature_shade_c`, stores the cell
values and area-weights them to Gemeinden. The numpy version was removed after
the measurement.

| | SQL | numpy |
| --- | ---: | ---: |
| Seconds per partition, mean (copy and compute, plus storing Gemeinde and cell values for numpy), current slim layout | 2.21 | 0.58 |
| Same, earlier wide cell table | 1.98 | 0.56 |
| Seconds per run of 25 lead hours, without loading the bridge, slim layout | 55.3 | 14.5 |
| Same, wide cell table (three runs) | 47.2 – 52.7 | 13.8 – 14.3 |
| Largest difference between the two results | 1.4 × 10⁻¹⁴ °C (summation order) | |

(Source: `docs/germany-layer-facts.md`, section "SQL vs. Python aggregation",
measured 2026-11-09. An earlier version of this table gave 47.2 – 57.5 s and
13.8 – 15.4 s per run; the upper values summed 27 partitions, because lead 0
of one run was measured three times. The raw rows were exported to
`de_aggregation_benchmark-2026-11-09.csv`, 714 rows, outside the repository,
before the benchmark table was dropped.)

SQL is about 3.8 times slower per partition on the slim layout (2.21 s
against 0.58 s), but the difference is small next to the rest of a run. A complete run of 25 lead
hours with SQL only, on 2026-11-14, took 141.5 s for the partitions (5.7 s per
partition on average, including the DWD download and decoding) and 35.8 s to
publish. numpy would save about 1.6 s per partition, about 41 s of the
177 s, which does not change how the pipeline is operated. SQL follows the
Berlin pipeline, keeps the unit conversion and the apparent-temperature
formula in one place, and needs no second implementation to keep in line. The
cost is a slower aggregation step; the benefit is less code (229 lines of
Python removed and 22 added, including one class, one config flag and one
command-line flag).

The test of the aggregation now uses hand-computed expected values instead of a
numpy comparison.

### Store per-cell values, in order to enable checks

`normalized.de_icon_cell_value` keeps the temperature and shade apparent
temperature of every grid cell for the two retained runs: 2,059,525 rows per
run (82,381 cells × 25 lead hours). Measured on 2026-11-14 with two runs
retained, the table takes 588 MB (heap and indexes, after one retention
cycle), against 194 MB for the Gemeinde values.

What the cell values are used for:

- **Check 7** (Berlin cross-layer): Berlin's planning-area values are
  recomputed from the same Germany cell values through Berlin's bridge and
  compared with Berlin as one Gemeinde. This is the strongest test that two
  bridges built in different projections agree.
- **Check 6** (plausibility) tests the cell values as well as the Gemeinde
  values.

Without the cell table, Gemeinde values could be aggregated straight from the
staging table, saving the 588 MB, the run table and the retention code for
cells. Check 7 would then need to be redesigned.

**Why the cell values are kept.** The per-cell values are stored so that
checks 6 and 7 can run on them. This is a trade-off: the checks are bought with
588 MB of disk (two runs retained), which the Gemeinde values alone would not
need.

**Revisit if storage becomes an issue.** If more runs or finer grids are
retained, or if disk becomes the limit, this trade-off needs to be revisited.
Dropping the cell table is then not free: the checks that depend on it, above
all check 7 and the cell part of check 6, will have to be redesigned first
(for example by recomputing cell values on demand from the staging table for a
sample of partitions, instead of reading them back from storage). Storing the
values as `real` (4 bytes) instead of `double precision` was measured at 329 MB
against 361 MB for two freshly written runs, only about 9% less, and was not
adopted.

## Consequences

- One implementation of the formula for Germany; changes happen in SQL.
- Per-run storage of cell values is kept to enable checks 6 and 7 and costs
  disk; the cost is measured and shown here, not estimated. If storage becomes
  an issue, the checks must be redesigned before the cell table can go.
- The earlier first layout of the cell table (a wide table with run, lead time
  and grid keys on every row) took 1,259 MB for two runs; the current slim
  layout (run and lead hour only, with a run table) took 361 MB freshly written.
