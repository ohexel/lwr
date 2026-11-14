# Germany layer: operation, checks and measurements

Extends the Berlin pipeline to Bund → Länder → Kreise → Gemeinden with the
current 24-hour DWD ICON-D2-RUC forecast and Zensus 2022 total population
(first theme), with employment per Gemeinde from the Statistik der
Bundesagentur für Arbeit (second theme, [below](#second-theme-employment)) and
with employees by sector per Kreis and Land (third theme,
[below](#third-theme-sectors)).
Berlin assets, views, sensor and outputs are unchanged; Germany objects carry
the `de_` prefix. Sources and licences: [data-sources.md](data-sources.md).
Measured numbers: [germany-layer-facts.md](germany-layer-facts.md).

## Run it

```bash
# once: sources, hierarchy, population, grid subset and bridge
uv run --env-file .env python -m src.germany.run_static

# per forecast run (UTC label; DWD keeps runs for about 24 hours)
uv run --env-file .env python -m src.germany.run_forecast \
  --run-time "$(date -u -d '3 hours ago' +%Y%m%dT%H00)"
```

`run_static` runs the Dagster job `germany_static`. It needs `ZENSUS2022_TOKEN`
only when the Zensus files are not yet present locally. It is safe to repeat:
a geography version that already exists from the same VG250 file is left
unchanged (a different file needs a new `geography_version`); population and
the bridge are rebuilt. The forecast runner materializes the Dagster job
`germany_forecast` once per lead time (the four raw DWD GRIB fields, shared
with Berlin and written only to files, plus the Germany aggregation), then
`germany_forecast_publish`. The Berlin availability sensor does not trigger
Germany jobs and stays stopped by default.

## Data flow

| Step | Object | Notes |
| --- | --- | --- |
| Grid subset | `normalized.de_icon_cell` | ICON cells intersecting a Gemeinde: the Berlin pipeline's cell polygons (EPSG:25833, read-only) transformed to EPSG:3035 |
| Bridge | `normalized.de_icon_cell_gemeinde_bridge` | intersection area and `fraction_of_gemeinde`, versioned by geography and grid |
| Cell forecast | `normalized.de_forecast_run`, `normalized.de_icon_cell_value` | one row per run (geography version, grid) and one row per run, lead hour and cell: temperature and shade apparent temperature, computed with Berlin's units and `normalized.calculate_apparent_temperature_shade_c`; lead-time label and valid time are derived from the lead hour |
| Gemeinde forecast | `normalized.de_gemeinde_forecast` | area-weighted from cells, like Berlin's PLR values |
| Serving | `analytical.de_current_{gemeinde,kreis,land,bund}_forecast_25h`, `analytical.de_current_{gemeinde,kreis,land,bund}_summary` | newest run with every Gemeinde × 25 lead times; the summaries hold the 24-hour maximum (`max_`) and minimum (`min_`) of each measure with its time (one rule for all four levels: highest or lowest value, earliest time on a tie; the `min_` columns are appended at the end of each view so `CREATE OR REPLACE VIEW` works on an installed database) |

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
| `vg250-2022-12-31.pmtiles` | Land, Kreis and Gemeinde outlines, zoom 4-10, property `key` = regional key only | once per geography version, or with `--rebuild-tiles` (needs `tippecanoe`) |
| `sectors.json` | schema 1, newest quarter: per Kreis, Land and Germany the sector values of `analytical.de_current_sectors` (null where not published, with `status`; `basis` for Land groups; `quality_note`). Not written when the sector data is not loaded | every export |
| `employment.json` | schema 1, newest BA reference date: per region `jobs_ratio` with `jobs_ratio_status`, and employees at place of work and of residence, commuters in and out, establishments (null where not published, with `status`); names come from `values.json` (same keys). Not written when the BA data is not loaded; the page then offers the temperature theme only | every export |
| `values.json` | schema 2, current complete run: per region the 24-hour maximum and minimum of shade apparent temperature and air temperature with its lead hour (an index into `valid_times_berlin`; read from the `de_current_*_summary` views), residents, names. Keys are descriptive, e.g. `max_apparent_temperature_shade_population_weighted_c`, `min_temperature_c`. The `min_` keys were added without a schema change (still 2): the page enables Tiefstwerte only when they are present | every export |

Generated files are git-ignored. The page joins values to outlines with
MapLibre feature-state (`promoteId: "key"`), so tiles are never rebuilt per run.

**What it shows.** All user-facing text is German. Two themes share the page
(switch "Thema"); this paragraph describes the temperature theme, the
employment theme is described [above](#second-theme-employment). The
temperature page title is
"Deutschland: Höchst- und Tiefsttemperaturen über die nächsten 24 Stunden".
A choropleth of the highest or lowest shade apparent temperature over the
next 24 hours at Land, Kreis or Gemeinde level (radio buttons, keyboard
operable). A switch chooses Höchstwerte or Tiefstwerte; the page reads
`min_<measure>_c` / `min_<measure>_<area|population>_weighted_c` (and the
matching `_lead_hour`) from `values.json`, the same naming as the `max_` keys.
A `values.json` from an older export has no `min_` keys; the page then
disables the Tiefstwerte option and says so.
Kreis and Land values switch between area- and
population-weighted roll-ups. Hover or click shows name, parents, the
extreme with its time (Europe/Berlin), the extreme air temperature and residents; a click
pins the region (the way to read a value on a touch screen). The forecast run
time is in the header. The local preview is `src/germany/preview_server.py`
(Range requests, which PMTiles needs).

**Colour scale.** Fixed and continuous, cool to warm: dark blue at -20 °C,
blue at -10 °C, light blue at 0 °C, pale blue at 8 °C, pale yellow at 16 °C,
orange at 22 °C, red at 30 °C (ColorBrewer RdYlBu, reversed, neutral moved to
16 °C; values outside the range take the end colours). It is the same on every
day and for all three levels, so a cold day (0-10 °C) is blue, never orange or
red, and a colour always means the same temperature. No warning classes are
drawn (D5).

**Upload.** Copy the whole `web/germany-dashboard/` folder (including
`data/`) to the web host. The host must answer HTTP Range requests for the
`.pmtiles` file; www.ohexel.com and ohexel.com both do (tested 2026-11-09:
`206 Partial Content` for first, middle and suffix ranges, no content
encoding). The page loads tiles from its own origin; the host sends no CORS
headers, so the tiles cannot be loaded from a page on another domain. For a
new run, re-export and upload `data/values.json` only; for a new BA vintage,
`data/employment.json`.

## Aggregation

The cell-to-Gemeinde aggregation runs in SQL
(`normalized.refresh_de_forecast_partition`). A numpy version was measured
against it during development and removed afterwards; the measured comparison
is in [germany-layer-facts.md](germany-layer-facts.md#sql-vs-python-aggregation)
and [ADR 018](adr/018-sql-aggregation-and-cell-storage.md).

## Things to know

- Every job applies the idempotent schema files in `sql/germany/` first,
  so a forecast run applies them once per lead time. This is simple and safe;
  it is why no separate install step exists.
- Building the boundary tiles needs `tippecanoe`, once per geography version.
- The serving views compute on read; a query takes about 1.6 to 2.7 seconds.
- `de_icon_cell_gemeinde_bridge_cell_idx` supports the foreign key from the
  bridge to the cells. Without it, rebuilding the bridge on an installed
  database takes about five times longer (648 s against 126 s).
- Per-cell values are stored for the two retained runs so that checks 6 and 7
  can run on them (588 MB). If storage becomes an issue, the trade-off has to
  be revisited and those checks redesigned first (ADR 018).

## Second theme: employment

Employees subject to social insurance by place of residence and place of
work, commuters and establishments, from the BA *Gemeindedaten* (yearly,
30 June; sources: [data-sources.md](data-sources.md#germany-layer-ba-gemeindedaten-employment)).
Decisions: [ADR 019](adr/019-indicator-model-and-ba-gemeindedaten.md).

```bash
# after run_static (needs the hierarchy): download, load and check one vintage
uv run --env-file .env python -m src.germany.run_employment            # newest known: 2025-06-30
uv run --env-file .env python -m src.germany.run_employment --reference-date 2026-06-30
```

`run_employment` runs the Dagster job `germany_employment`; it is safe to
repeat (raw rows and indicator values of the reference date are replaced).
`ba_gemeindedaten_release_sensor` (off by default, like the DWD sensor) sends
one HEAD request a day for the vintage after the newest one loaded and
starts the job when the file is published. A vintage not yet in the contract
loads if its structure matches; its key-coverage check then fails until its
territorial changes are reviewed and added to `src/germany/contract.py`.

| Step | Object | Notes |
| --- | --- | --- |
| Raw | `raw.de_ba_gemeindedaten` | both data sheets as delivered; `column_01` … `column_13` = BA column numbers; `*` = suppressed |
| Catalogue | `normalized.de_indicator_source`, `normalized.de_indicator` | source citation, terms, update cycle; 13 indicators with label, unit, source column and whether they add up across levels |
| Values | `normalized.de_indicator_value` | one row per geography version, level, region, indicator and reference date; `value_status` = `value`, `suppressed` or `not_reported` (with `status_reason`); Kreis, Land and Germany are the BA's published rows; a Kreis with one Gemeinde takes its Gemeinde row |
| Serving | `analytical.de_current_employment` | newest reference date, one row per region and level: employees at place of work and of residence, commuters in and out, establishments, each with its status, and `jobs_ratio` = workplace / residence (own calculation) with `jobs_ratio_status` |

| # | Check | Rule | Severity |
| --- | --- | --- | --- |
| 8 | `de_ba_source_contract` | both sheets: 16 Länder, one Germany and one unassigned row, values only digits or `*`, `*` only in Gemeinde rows; a pinned vintage also its checksum and row counts | error |
| 9 | `de_ba_column_identities` | every row: residence = men + women; Germans + foreigners ≤ residence; commuters in ≤ workplace − same place; commuters out ≤ residence − same place | error |
| 10 | `de_ba_key_coverage` | BA Gemeinde keys against the hierarchy: only the unincorporated areas and the territorial changes listed in the contract for this vintage; every Kreis and Land matched | error |
| 11 | `de_ba_source_totals` | additive indicators: Σ Gemeinden = Kreis exactly where nothing is suppressed, ≤ Kreis where something is; Σ Kreise = Land; Σ Länder + unassigned row = Germany | error |

**Example query.** The ten Kreise with the most jobs per employed resident:

```sql
SELECT region_name, employees_workplace, employees_residence,
       ROUND(jobs_ratio::numeric, 2) AS jobs_ratio
FROM analytical.de_current_employment
WHERE level = 'krs'
ORDER BY jobs_ratio DESC
LIMIT 10;
```

**On the map.** A theme switch (Temperatur / Beschäftigung, link
`#beschaeftigung`) changes title, header, legend and details. The colour is
`jobs_ratio` on a fixed logarithmic scale (ColorBrewer PuOr, ¼ to 4, 1 in the
middle; orange: more employed residents than jobs, purple: more jobs). Two
greys: values suppressed by the BA, and regions the BA does not report. The
details show the published values as delivered, the ratio marked as own
calculation; the footer cites the BA and links its statistics homepage, as
the terms of use ask.

## Third theme: sectors

Employees subject to social insurance at place of work by sector, quarterly,
from two BA products (sources:
[data-sources.md](data-sources.md#germany-layer-ba-sector-data-regionalreport-and-wz-booklets);
decisions: [ADR 020](adr/020-sector-data-kreis-groups-and-land-divisions.md)):

- **Kreise:** *Regionalreport über Beschäftigte*, one file per Kreis: WZ
  sections and three manufacturing groups, among them "Metall- und
  Elektroindustrie sowie Stahlindustrie" (WZ 24–30, 32, 33).
- **Länder and Germany:** *Beschäftigte nach Wirtschaftszweigen*: every WZ
  division, so motor vehicles (29), mechanical engineering (28), chemicals
  (20) and steel (24.1) exactly.
- **No Gemeinden:** the BA publishes no sector figures per Gemeinde. At Kreis
  level the four industries exist only inside their groups.

```bash
# after run_static (needs the hierarchy): download, load and check one quarter
uv run --env-file .env python -m src.germany.run_sectors                         # newest known: 2026-03-31
uv run --env-file .env python -m src.germany.run_sectors --file-date 2026-06-30
```

`run_sectors` runs the Dagster job `germany_sectors`: the 17 WZ booklets for
the quarter and for a year earlier, then one Regionalreport per Kreis, one
request at a time with a 3 s pause (434 files, about 22 minutes for a new
quarter; at most 600 requests per run; files on disk are not downloaded again
and all are kept). Every file is checked against the structural contract
before it is kept; one bad file stops the run. `ba_sectors_release_sensor`
(off by default) sends two HEAD requests a day for the next quarter.

**When the BA throttles.** The BA limits the volume of requests: HTTP 429
("too many requests") is answered with waits of 1, 5 and 15 minutes (or the
server's `Retry-After`); if it persists, a cool-down of at least an hour is
written to `data/raw/germany/ba_rate_limit.json`, which every download and
the sensor respect before sending anything. `run_sectors` then stops with
exit status 2 and a message like "312 of 400 Kreis files for 2026-03-31 are
on disk … Run the same command again after 2026-11-14T23:05+01:00". Running
it again later continues with the missing files. To copy the files from
another checkout instead, put them under `data/raw/germany/ba_regionalreport/`
and `data/raw/germany/ba_wz_heft/` with the same names: they are checked like
downloads and not requested again.

| Step | Object | Notes |
| --- | --- | --- |
| Raw | `raw.de_ba_regionalreport`, `raw.de_ba_wz_heft` | as delivered; Regionalreport tables 2.1 and 2.3, one row per table row and quarter; WZ sheet "SVB - Tabelle I", columns 1–11 as text array |
| Values | `normalized.de_indicator_value` | Kreise: 23 indicators of table 2.3 for every quarter (newest file wins); Länder and Germany: WZ total, 88 divisions, 24.1, "keine Angabe"; every value with a status |
| Derived | `normalized.de_indicator_sum_component` | which WZ divisions make up each group at Land level (`wz_sum_*`, own calculations) |
| Quality notes | `normalized.de_indicator_quality_note` | reviewed discrepancies in the BA's figures, from the contract; shown on the map |
| Serving | `analytical.de_current_sectors` | newest quarter per Kreis, Land and Germany: total, manufacturing, the three groups (with `<group>_basis` at Land level: `sum_of_divisions` or `sum_of_kreise`), the share of metal, electrical and steel, the same group a year earlier, the four divisions (Land and Germany; `not_published_at_level` for Kreise), `quality_note` |

| # | Check | Rule | Severity |
| --- | --- | --- | --- |
| 12 | `de_ba_sector_source_contract` | one complete Regionalreport per Kreis (all rows of table 2.3, five quarters); Land totals equal across the Kreis files of a Land; one WZ booklet per Land and Germany with the total and the four divisions | error |
| 13 | `de_ba_sector_identities` | per Kreis and quarter: total = sections + unassigned; C = its three groups; N = without temporary agency work + temporary agency work; B–F and G–U = their sections; exact, or bounded where a part is suppressed | error |
| 14 | `de_ba_sector_source_totals` | Σ Kreise = Land; Land totals and groups of the Regionalreport = WZ booklet; WZ divisions + "keine Angabe" = total; Germany = Σ Länder + employees not assignable to a Land (≤ 0.01 %, equal to the Gemeindedaten's unassigned row where both have the date); Kreis totals = Gemeindedaten employees at place of work; reviewed discrepancies accepted only at their documented size | error |
| 15 | `de_ba_sector_key_coverage` | all 400 Kreise, 16 Länder and Germany have a total for the newest quarter | error |

**Example query.** Kreise with the highest share of metal, electrical and
steel:

```sql
SELECT region_name, metal_electrical_steel, employees_total,
       ROUND(100 * metal_electrical_steel_share::numeric, 1) AS share_percent
FROM analytical.de_current_sectors
WHERE level = 'krs' AND metal_electrical_steel_share_status = 'value'
ORDER BY metal_electrical_steel_share DESC
LIMIT 10;
```

**On the map.** Theme "Industrie" (link `#industrie`), Kreis and Land level:
the share of metal, electrical and steel on a fixed scale from 0 to 40 %
(ColorBrewer Greens). Values the BA hides are grey with "von der BA
geheimgehalten"; a Land sum with a hidden part reads "nicht berechenbar:
Teilwerte von der BA geheimgehalten". Kreis details list the groups and say
that the four industries are published only for Länder; Land details add
them. A reviewed quality note appears with its Kreis.

## Decisions

[ADR 014](adr/014-equal-area-projection-for-germany.md) equal-area projection ·
[ADR 015](adr/015-rollup-weighting.md) roll-up weighting ·
[ADR 016](adr/016-map-pmtiles-and-json-values.md) map: PMTiles and JSON ·
[ADR 017](adr/017-zensus-2022-population-and-source-checks.md) Zensus and source
checks · [ADR 018](adr/018-sql-aggregation-and-cell-storage.md) SQL aggregation
and cell storage · [ADR 019](adr/019-indicator-model-and-ba-gemeindedaten.md)
indicator model and BA Gemeindedaten ·
[ADR 020](adr/020-sector-data-kreis-groups-and-land-divisions.md) sector data.
