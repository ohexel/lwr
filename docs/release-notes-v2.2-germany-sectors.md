# v2.2: Add sector theme

## Summary

v2.2 adds a third theme to the Germany layer: **employees by sector per Kreis
and Land**, quarterly, from two products of the Statistik der Bundesagentur
für Arbeit (BA). It answers, as far as public data allows, how many jobs in
a region depend on the motor vehicle industry, mechanical engineering,
chemicals and steel:

- **per Kreis** the share of jobs in the metal, electrical and steel
  industries (WZ 2008 divisions 24–30, 32, 33) and the other groups the BA
  publishes for Kreise (*Regionalreport über Beschäftigte*);
- **per Land and for Germany** additionally motor vehicles (29), mechanical
  engineering (28), chemicals (20) and steel (24.1) exactly (*Beschäftigte
  nach Wirtschaftszweigen*).

There are no sector figures per Gemeinde, and per Kreis the four industries
exist only inside their groups; the page and the docs say so. Berlin and the
other themes are unchanged in behaviour.

## What is new

- **Sources:** two quarterly BA products without login; structural contracts
  for both (the exact row sequence of the Kreis table, column layout, dates,
  value signs).
- **Considerate acquisition:** 434 files per quarter, one request at a time
  with a 1.5 s pause, backoff on 429/5xx and `Retry-After`, never repeated for
  a file on disk; every file checked before it is kept; all files kept.
- **Data model:** `raw.de_ba_regionalreport`, `raw.de_ba_wz_heft`; sector
  values in the indicator tables of ADR 019, which now take sources that
  publish only some levels; `de_indicator_sum_component` declares Land
  groups as sums of published values; `de_indicator_quality_note` carries
  reviewed discrepancies; `analytical.de_current_sectors`.
- **Suppressed values as a value type:** from raw (`*`) to the page (own grey,
  "von der BA geheimgehalten", and "nicht berechenbar: Teilwerte
  geheimgehalten" for sums); checks treat them as bounds, not as zero.
- **Checks 12–15:** file structure, identities within each Kreis, totals
  across Kreise, Länder, both products and the Gemeindedaten, coverage.
- **Automation:** job `germany_sectors`, command
  `python -m src.germany.run_sectors [--file-date …]`,
  `ba_sectors_release_sensor` (two HEAD requests a day, off by default).
- **Map:** theme "Industrie" (link `#industrie`) at Kreis and Land level, fixed
  scale 0–40 %; Gemeinde level disabled with an explanation.
- **Decision:** ADR 020.

## Verified (14 Nov 2026)

On the maintainer's installed database; files of 31 Mar 2026 and 31 Mar 2025.
Details in [germany-layer-facts.md](germany-layer-facts.md).

| What | Result |
| --- | --- |
| Default test suite | 193 passed |
| Germany integration tests | 7 passed (one new: synthetic workbooks through raw load, refresh and check functions) |
| Germany acceptance tests | 26 passed |
| Download of 434 files | 649 s, 233 MB, no retries needed |
| `run_sectors` (files on disk) | 69 s; checks 12–15 passed |
| Regionalreport vs Gemeindedaten | the Kreis totals of 30 Jun 2025 equal the employees at place of work for all 400 Kreise; Germany minus the Länder equals the Gemeindedaten's unassigned row (2,160) |
| Regionalreport vs WZ booklets | Land totals equal (32 of 32); groups equal wherever both are complete (37 of 37, apart from the documented Hessen quarter) |
| Map in headless Chromium (1400 px, 390 px) | all three themes, direct link, Gemeinde level disabled for the sector theme; suppressed and quality-note display tested by substituting values; no console errors, no horizontal overflow |

## Found in the source

- **Main-Kinzig-Kreis, 31 Mar 2026:** the BA reports 93,730 employees after
  142,459 a quarter earlier (-34 %, in every section and by place of
  residence), while Hessen barely changes; the Kreise of Hessen fall 47,649
  short of the Land. The values are shown as published with a note; the check
  accepts exactly this documented gap. A later file that revises the quarter
  replaces the values.
- The BA hides the metal, electrical and steel group for Wolfsburg,
  Ingolstadt and Passau; they are grey on the map.

## Limitations

- No Gemeinde level. Per Kreis, motor vehicles, mechanical engineering and
  steel are part of "Metall-, Elektro- und Stahlindustrie" (which also holds
  metal products, electronics, electrical equipment and other vehicles);
  chemicals are part of "Vorleistungsgüter" (with paper, glass and plastics).
- Land values of the groups are own calculations: sums of published
  divisions, or of the Land's Kreise where a division is hidden.
- Employees subject to social insurance only.
- About 210 MB of files per quarter stay on disk.

## Next steps

- Earlier quarters (each file holds five) are loaded; older files could
  extend the series.
- Test triage remains out of scope until further notice.
