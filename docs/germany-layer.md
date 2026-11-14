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
| Cell forecast | `normalized.de_forecast_run`, `normalized.de_icon_cell_value` | one row per run (geography version, grid) and one row per run, lead hour and cell: temperature and shade apparent temperature, computed with Berlin's units and `normalized.calculate_apparent_temperature_shade_c`; lead-time label and valid time are derived from the lead hour |
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
| 2 | `de_hierarchy_integrity` | what foreign keys cannot guarantee: no Kreis without Gemeinden, no Land without Kreise, a Gemeinde's Land equals its Kreis's Land, VG250 key parts agree, Σ Gemeinde area = Kreis area within 1e-9 (relative); foreign keys cover that parents exist | error |
| 3 | `de_source_totals` | Σ normalized Gemeinde population = published Kreis and Land totals, exactly; published Germany total − Σ Länder = the documented 8,258, exactly | error |
| 4 | `de_bridge_coverage` | Σ intersection / Gemeinde area within [0.999, 1] for every Gemeinde | error |
| 5 | `de_forecast_completeness` | served run has every Gemeinde × 25 lead times | error |
| 6 | `de_forecast_plausibility` | current run, Gemeinden and cells: no NULLs; temperature −50…+50 °C; apparent temperature −70…+60 °C | error |
| 7 | `de_berlin_cross_layer_consistency` | Berlin's Gemeinde value vs. the area-weighted roll-up of its 542 PLRs, both from the same cell values; max difference ≤ 0.01 °C | warning |

**Check 3.** Kreis and Land totals in Zensus 2022 total population are
published without Cell Key perturbation and match the Gemeinde sums exactly,
so the tolerance there is 0. The published Germany total (82,719,540) exceeds
the sum of the Länder (82,711,282) by 8,258. Destatis' *Qualitätsbericht
Zensus 2022* (published 20/11/2024; section 1.1 "Grundgesamtheit", section 7.2
"Statistikinterne Kohärenz") counts Bundeswehr, police and foreign-service
staff working abroad, with their families living there, as part of the
population and states that register-based results contain them at Bund
level. The report does not give their number, so the 8,258 is consistent with
that definition but not confirmed against a published figure. Because the
extracts are pinned by SHA-256, the check expects exactly this difference:
any other value means the source changed and fails the check. Population
weights in the roll-ups use Gemeinde values, so the Bund population in
`de_current_bund_forecast_25h` is 82,711,282, not the published total.

**Check 7.** The PLR roll-up is recomputed from the Germany cell values through
Berlin's existing bridge (`normalized.icon_plr_area_bridge`, read-only), and
the 542 PLR values are weighted by PLR area. Both sides therefore use identical
cell values; the only difference is the outline (generalised VG250 1:250 000
vs. the LOR planning areas). Over the first three runs (75 lead times) the
largest difference was 0.0014 °C for either measure; the tolerance 0.01 °C
leaves headroom for days with steeper gradients along the outline. If Berlin's
own stored output (`analytical.plr_weather`) exists for the same run time, it
is compared as well; otherwise the check reports it as not available.

## Map

A separate static page in `web/germany-dashboard/` (the Berlin dashboard is
not touched). MapLibre GL JS 4.7.1 and PMTiles 3.2.1 are loaded from
jsDelivr with pinned versions and Subresource Integrity hashes; there is no
basemap and no API key.

```bash
# export (and build the boundary tiles on first use), then preview locally
uv run --env-file .env python -m src.export_germany_dashboard --serve
# -> http://127.0.0.1:8766/
```

| File in `web/germany-dashboard/data/` | Content | Rebuilt |
| --- | --- | --- |
| `vg250-2022-12-31.pmtiles` | Land, Kreis and Gemeinde outlines, zoom 4-10, property `k` = regional key only | once per geography version, or with `--rebuild-tiles` (needs `tippecanoe`) |
| `values.json` | current complete run: per region the 24-hour maximum of shade apparent temperature and air temperature with their hour, residents, names, a point inside the region | every export |

Generated files are git-ignored. The page joins values to outlines with
MapLibre feature-state (`promoteId: "k"`), so tiles are never rebuilt per run.

**What it shows.** A choropleth of the maximum shade apparent temperature
over the next 24 hours at Land, Kreis or Gemeinde level (radio buttons,
keyboard operable). Kreis and Land values switch between area- and
population-weighted roll-ups. Hover or click shows name, parents, maximum
with its time (Europe/Berlin), maximum air temperature and residents;
"Find a region" does the same by name and zooms to it. The forecast run time
is in the header.

**Colour scale.** ColorBrewer YlOrRd, continuous, sequential in lightness
(readable with the common colour-vision deficiencies). The range is this
run's Gemeinde minimum to maximum, widened to whole 5 °C steps, and shared by
all three levels. Because it follows the run, the same colour can mean
different temperatures on different days; the legend says so. No warning
classes are drawn (D5).

**Upload.** Copy the whole `web/germany-dashboard/` folder (including
`data/`) to the web host. The host must answer HTTP Range requests for the
`.pmtiles` file; www.ohexel.com and ohexel.com both do (tested 2026-11-09:
`206 Partial Content` for first, middle and suffix ranges, no content
encoding). The page loads tiles from its own origin; the host sends no CORS
headers, so the tiles cannot be loaded from a page on another domain. For a
new run, re-export and upload `data/values.json` only.

## Aggregation

The cell-to-Gemeinde aggregation runs in SQL
(`normalized.refresh_de_forecast_partition`). A numpy version was measured
against it during development and removed afterwards; the measured comparison
is in [germany-layer-facts.md](germany-layer-facts.md#sql-vs-python-aggregation)
and ADR 018 (to be written in M6).
