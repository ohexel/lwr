# Draft: v2.0-germany (to be published by the maintainer)

## Summary

v2.0 adds a **Germany layer** to the Berlin capstone: the same pipeline, extended
to Bund → 16 Länder → 400 Kreise → 10,990 Gemeinden. For each Gemeinde it
combines the current 24-hour DWD ICON-D2-RUC forecast (air temperature and
shade apparent temperature, area-weighted from the model grid) with Zensus 2022
total population, rolls the values up with an area and a population weighting,
checks them against the sources, and shows them on a static MapLibre map.

The Berlin pipeline, its views, sensor, tests and dashboard are unchanged
(tag `v1.0-capstone-submitted`). Germany objects carry the prefix `de_`.

## What is new

- **Sources:** BKG VG250 (status 31 Dec 2022) and Zensus 2022 table 1000A-1003
  (reference date 15 May 2022), both pinned by checksum; DWD ICON-D2-RUC, shared
  with Berlin.
- **Data model:** `raw.de_vg250`, `raw.de_zensus2022_population`; normalized
  hierarchy (`de_land`, `de_kreis`, `de_gemeinde`) in EPSG:3035, population with
  an explicit status, cell-Gemeinde bridge, forecast facts; analytical views
  `de_current_{gemeinde,kreis,land,bund}_forecast_25h` and
  `de_current_{gemeinde,kreis,land,bund}_summary`.
- **Checks against the sources:** seven asset checks plus two raw-file contract
  checks (README, "Germany layer").
- **Commands:** `python -m src.germany.run_static`,
  `python -m src.germany.run_forecast --run-time …`,
  `python -m src.export_germany_dashboard --serve`.
- **Map:** `web/germany-dashboard/`, MapLibre with PMTiles outlines and a JSON
  values file; no basemap and no API key.
- **Decisions:** ADRs 014–018 in `docs/adr/`.

## Verified (14 Nov 2026)

On the maintainer's installed database (PostgreSQL 16.9, PostGIS 3.5.2); forecast
run 2026-11-14 09:00 UTC. Measured values are in
[germany-layer-facts.md](germany-layer-facts.md); those from the final
verification are below.

| What | Result |
| --- | --- |
| Default test suite | 169 passed |
| Germany integration tests (4 files) | 5 passed |
| Germany acceptance tests (installed data) | 7 passed |
| `run_static` | 143.4 s; all static checks passed; counts unchanged |
| Forecast run, 25 lead hours | 141.5 s for the partitions including the DWD download, 35.8 s to publish; checks 5–7 passed |
| Source totals (check 3) | exact for 400 of 400 Kreise and 16 of 16 Länder; Germany total differs from the sum of Länder by the documented 8,258 |
| Bridge coverage (check 4) | 0.9999999999998317 to 1.0000000000001266 for every Gemeinde |
| Berlin cross-layer (check 7) | largest difference 0.00090 °C (temperature) and 0.00086 °C (apparent), tolerance 0.01 °C |
| Map export with tile rebuild | 41 s; tiles 13,139,513 bytes; `values.json` 3,511,495 bytes (281,537 gzipped) |
| Map in headless Chromium | no console errors; no horizontal overflow at 390 px |
| Berlin | behaviour unchanged. Against `main`, the only lines removed from files outside the Germany paths are the unused `from __future__ import annotations` import and the blank line after it (a cleanup in 38 modules, now banned by a ruff rule). Everything else is added: registrations in `definitions.py`, a commented line in `.env.example`, the ruff dev dependency (`pyproject.toml`, `uv.lock`), `tests/test_lint.py`, and text in the README, `architecture.md`, the ADR index and `docs/data-sources.md` |

## Changes after the review of 10 Nov

- The numpy aggregation was removed; SQL is the only implementation (ADR 018).
- Germany grid cells are the Berlin cell polygons transformed to EPSG:3035
  instead of a rebuild from vertices; an index on the bridge's foreign key makes
  rebuilding an installed database take 126 s instead of 648 s (ADR 014).
- The one-off migration for an earlier table layout was deleted.
- The raw Zensus table was renamed `raw.de_zensus2022_population`.
- Check 2 was reduced to what foreign keys cannot guarantee, plus one added rule
  (a Gemeinde's Land equals its Kreis's Land).
- The 24-hour maxima for Kreis, Land and Bund are computed in SQL
  (`de_current_*_summary`), as already done for Gemeinden.
- `values.json` uses descriptive keys (schema 2); the map's region search was
  removed; the preview server has its own module.
- `run_static` replaces a `python -c` snippet. The hierarchy step is safe to
  repeat: an existing geography version from the same file is left unchanged.
  Before, a second static run would have failed on an installed database
  because forecasts and the bridge reference the Gemeinden.

## Limitations

- Forecast only. There is no Germany-wide historical reference.
- Population is Zensus 2022 (15 May 2022). VG250 is a generalised 1:250,000
  geometry with the status of 31 Dec 2022.
- Shade apparent temperature does not include solar radiation.
- The values are measurements, with no thresholds, classes or exposure scores.
- A portfolio project run on a local machine, not an operated service.

## Not included, next steps

- A review and cut of the Germany tests is planned as a separate step.
- `docs/germany-layer-facts.md` still holds the measurements of 9 Nov 2026 and
  will be regenerated after the code freeze.
- Open decision (ADR 018): whether to keep per-cell values, which cost 588 MB
  for two runs, against the checks that need them.
- The aggregation tie rule for the 24-hour maximum (earliest hour wins) is
  implemented in SQL and has no test of its own.
- Candidate next theme: employees and establishments per Gemeinde from the
  Bundesagentur für Arbeit.
