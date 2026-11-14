# v2.1: Add employment per Gemeinde theme

## Summary

v2.1 adds a second theme to the Germany layer: **employment per Gemeinde**
from the Statistik der Bundesagentur für Arbeit (BA). For every Gemeinde,
Kreis and Land it serves employees subject to social insurance by place of
residence and by place of work, commuters in and out and the number of
establishments (reference date 30 June 2025), checks them against the BA's
own published totals, and shows employees at place of work per employee at
place of residence on the map, next to the temperature theme.

The values go into a **theme-independent indicator model**
(`normalized.de_indicator_source`, `de_indicator`, `de_indicator_value`):
one row per region, level, indicator and reference date, with an explicit
status where the source suppresses a value or does not report a region. A
further source adds catalogue rows and a refresh function; the tables stay as
they are (ADR 019).

Berlin and the temperature theme are unchanged in behaviour.

## What is new

- **Source:** BA *Gemeindedaten der sozialversicherungspflichtig
  Beschäftigten nach Wohn- und Arbeitsort* (product 229), yearly xlsx, no
  login. Contract on the file's structure for every vintage; checksum, row
  counts and territorial differences pinned for 2025.
- **Data model:** `raw.de_ba_gemeindedaten` (as delivered, `*` kept);
  the indicator tables; `analytical.de_current_employment` with
  `jobs_ratio` (own calculation) and its status.
- **Checks (8–11):** the file's structure, identities between its columns,
  its keys against the hierarchy (only documented exceptions), and its
  Gemeinde, Kreis and Land values against its published totals.
- **Automation:** job `germany_employment`, command
  `python -m src.germany.run_employment [--reference-date …]`, and
  `ba_gemeindedaten_release_sensor`: one HEAD request a day for the next
  vintage (due 15 Jan 2027), off by default.
- **Map:** theme switch Temperatur / Beschäftigung (link `#beschaeftigung`),
  a fixed logarithmic colour scale (¼ to 4) for the ratio, two greys for
  suppressed and not reported regions, BA citation and link as its terms of
  use ask. `data/employment.json` is written next to `values.json`.
- **Decision:** ADR 019.
- **Dependency:** `openpyxl`.

## Verified (14 Nov 2026)

On the maintainer's installed database; BA vintage 30 June 2025, forecast run
2026-11-14 09:00 UTC. Details in [germany-layer-facts.md](germany-layer-facts.md).

| What | Result |
| --- | --- |
| Default test suite | 181 passed |
| Germany integration tests | 6 passed (one new: a synthetic BA workbook through raw load, refresh and all check functions) |
| Germany acceptance tests (installed data) | 23 passed |
| `run_employment` | 12.1 s; checks 8–11 passed |
| Source totals | Gemeinden to Kreis exact wherever nothing is suppressed, never above the Kreis otherwise; Kreise to Land and Länder plus the unassigned row to Germany exact for all 10 additive indicators |
| Key coverage | 4 BA Gemeinden without a VG250 key, 38 VG250 Gemeinden merged after 2022, 204 unincorporated areas: exactly the documented lists |
| Map in headless Chromium (1400 px and 390 px) | both themes and the direct link; details, tooltip and grey for a suppressed, an unincorporated and a merged Gemeinde (tested by substituting these statuses in `employment.json`); no console errors, no horizontal overflow |

## Also changed

- The map keeps its own height instead of stretching with the control
  column. Before, a details panel that grew on hover resized the map, so a
  click could land on a different region than the one hovered (also in
  v2.0).
- The map leaves room for the attribution bar; the southern edge of Bavaria
  was hidden under it.
- The 2,160-employee gap between the Länder and Germany in the BA file,
  open in the source survey, is the BA's own row "keine Angabe/keine
  Zuordnung möglich".

## Limitations

- Employees subject to social insurance only: no civil servants,
  self-employed or marginal jobs.
- The BA suppresses small values: 1,261 Gemeinden have no ratio; Kreise,
  Länder and Germany are complete.
- The BA uses the territorial status of its reference date, the map VG250 of
  2022. 38 Gemeinden merged since then have no value; a Gemeinde that
  absorbed another one shows its 2025 value on its 2022 outline. No mapping
  between old and new Gemeinden is built.
- The BA counts employees at the Gemeinde of the establishment number they
  are reported under; head offices can therefore show too many jobs.

## Next steps

- The BA's quarterly reports per Kreis (employees by sector groups) answer
  the question about automotive, engineering, chemicals and steel at Kreis
  level; they would go into the same indicator tables.
- Earlier vintages of the same file (the 2024 file is online) would give a
  time series without new code.
- Test triage remains out of scope until further notice.
