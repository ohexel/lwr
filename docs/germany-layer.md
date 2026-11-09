# Germany layer: operation, checks and measurements

Extends the Berlin pipeline to Bund → Länder → Kreise → Gemeinden with the
current 24-hour DWD ICON-D2-RUC forecast and Zensus 2022 total population.
Berlin assets, views, sensor and outputs are unchanged; Germany objects carry
the `de_` prefix. Sources and licences: [data-sources.md](data-sources.md).
Measured numbers: [germany-layer-facts.md](germany-layer-facts.md).

## Run it

```bash
# once: sources, hierarchy, population, grid subset and bridge
uv run --env-file .env python -c "
from src.bootstrap import ensure_dagster_home; from pathlib import Path
import dagster as dg; ensure_dagster_home(Path('.'))
from src.dagster_pipeline.definitions import defs
defs.resolve_job_def('germany_static').execute_in_process(instance=dg.DagsterInstance.get())"

# per forecast run (UTC label; DWD keeps runs for about 24 hours)
uv run --env-file .env python -m src.germany.run_forecast \
  --run-time "$(date -u -d '3 hours ago' +%Y%m%dT%H00)"
```

`germany_static` needs `ZENSUS2022_TOKEN` only when the Zensus files are not
yet present locally. The forecast runner materializes the Dagster job
`germany_forecast` once per lead time (the four raw DWD GRIB fields, shared
with Berlin and written only to files, plus the Germany aggregation), then
`germany_forecast_publish`. The Berlin availability sensor does not trigger
Germany jobs and stays stopped by default.

## Data flow

| Step | Object | Notes |
| --- | --- | --- |
| Grid subset | `normalized.de_icon_cell` | ICON cells intersecting a Gemeinde, rebuilt from the grid's lon/lat vertices in EPSG:3035 (not reprojected from Berlin's EPSG:25833 cells) |
| Bridge | `normalized.de_icon_cell_gemeinde_bridge` | intersection area and `fraction_of_gemeinde`, versioned by geography and grid |
| Cell forecast | `normalized.de_icon_cell_weather` | per cell and lead time: temperature and shade apparent temperature, computed with Berlin's units and `normalized.calculate_apparent_temperature_shade_c` |
| Gemeinde forecast | `normalized.de_gemeinde_forecast` | area-weighted from cells, like Berlin's PLR values |
| Serving | `analytical.de_current_{gemeinde,kreis,land,bund}_forecast_25h`, `analytical.de_current_gemeinde_summary` | newest run with every Gemeinde × 25 lead times |

Kreis, Land and Bund values are served twice: weighted by Gemeinde area (equal
to direct area weighting, because Gemeinden partition their Kreis in VG250)
and weighted by Zensus 2022 total population. Gemeinden without a census
record (population NULL) contribute no population weight; their population is
never treated as zero. The serving views contain measurements only (D5).

## Retention

Database rows of the newest complete run and the complete run before it are
kept, plus a newer run while it is still loading
(`normalized.prune_de_forecast`, applied on publish). GRIB files are never
deleted by the Germany layer; they share Berlin's directory layout and its
existing file retention rule.

## Checks and thresholds

| # | Check | Rule | Severity |
| --- | --- | --- | --- |
| 1 | `de_key_coverage` | every Land/Kreis/Gemeinde key exists in VG250 and Zensus; unincorporated areas without census record listed, non-blocking | error |
| 2 | `de_hierarchy_integrity` | parents exist, no empty parent, VG250 key parts agree, Σ Gemeinde area = Kreis area within 1e-9 (relative) | error |
| 3 | source totals | M4 | |
| 4 | `de_bridge_coverage` | Σ intersection / Gemeinde area within [0.999, 1] for every Gemeinde | error |
| 5 | `de_forecast_completeness` | served run has every Gemeinde × 25 lead times | error |
| 6 | `de_forecast_plausibility` | current run, Gemeinden and cells: no NULLs; temperature −50…+50 °C; apparent temperature −70…+60 °C | error |
| 7 | `de_berlin_cross_layer_consistency` | Berlin's Gemeinde value vs. the area-weighted roll-up of its 542 PLRs, both from the same cell values; max difference ≤ 0.01 °C | warning |

**Check 7.** The PLR roll-up is recomputed from the Germany cell values through
Berlin's existing bridge (`normalized.icon_plr_area_bridge`, read-only), and
the 542 PLR values are weighted by PLR area. Both sides therefore use identical
cell values; the only difference is the outline (generalised VG250 1:250 000
vs. the LOR planning areas). Over the first three runs (75 lead times) the
largest difference was 0.0014 °C for either measure; the tolerance 0.01 °C
leaves headroom for days with steeper gradients along the outline. If Berlin's
own stored output (`analytical.plr_weather`) exists for the same run time, it
is compared as well; otherwise the check reports it as not available.

## SQL vs. Python aggregation (open decision 2)

Both implementations run on the same decoded fields for every partition
(`--skip-python-benchmark` turns the numpy one off). SQL is canonical and
stored; the numpy result goes to a temporary table and is compared. Timings
are kept in `normalized.de_aggregation_benchmark`. Results are in
[germany-layer-facts.md](germany-layer-facts.md#sql-vs-python-aggregation).
