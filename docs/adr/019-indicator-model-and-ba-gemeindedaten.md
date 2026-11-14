# ADR 019: A theme-independent indicator model, filled first with the BA Gemeindedaten

Status: Accepted on the `germany-employment` branch (14 Nov 2026).

## Context

The Germany layer had one theme, the temperature forecast, with tables shaped
for it (one column per measure). A second theme, employment per Gemeinde, was
chosen from the source survey of 14 Nov 2026: the *Gemeindedaten der
sozialversicherungspflichtig Beschäftigten nach Wohn- und Arbeitsort* of the
Statistik der Bundesagentur für Arbeit (product 229), one xlsx per year with
Gemeinde, Kreis, Regierungsbezirk, Land and Germany rows for 30 June. Further
themes (sectors, pay, housing costs) will follow, from other sources and with
other measures.

Three properties of the source shape the design:

- **Suppression.** The BA replaces values below 3 and values that would allow
  them to be recomputed with `*`. For Gemeinden with fewer than three
  establishments, or where one establishment dominates, it deletes all
  place-of-work values (columns 9–11). Its Kreis and Land totals still
  include the suppressed values (BA, *Erläuterungen*). In the 2025 file 1,257
  Gemeinden have no value for employees at place of work.
- **Territorial status.** The BA uses the territorial status of its reference
  date (30 June 2025); the hierarchy uses VG250 of 31 Dec 2022. 10,748 of the
  10,752 BA Gemeinden match a VG250 key. Four BA Gemeinden are new, 38 VG250
  Gemeinden no longer appear. Some of the 38 merged into the four new
  Gemeinden, others into Gemeinden that kept their key; the file does not say
  which.
- **Terms of use.** Free use with source citation; contents must not be
  altered; own calculations must be marked as such; an online publication
  should link to the BA statistics homepage.

## Decision

### One long value table with an explicit status

`normalized.de_indicator_source` and `normalized.de_indicator` describe each
source and indicator (label, unit, source column, whether it adds up across
levels). `normalized.de_indicator_value` holds one row per geography version,
level, region, indicator and reference date. Every region of the hierarchy
gets a row, so a missing value is a row with a status, never an absent row
and never 0:

| `value_status` | Meaning |
| --- | --- |
| `value` | published number |
| `suppressed` | the source published `*` |
| `not_reported` | the source has no row for the region; `status_reason` says why (`no_source_record_unincorporated`, `no_source_record`) |

A further source adds catalogue rows and its own refresh function; the tables
stay as they are. Wide serving views per theme (here
`analytical.de_current_employment`) are built on top for analysts and the map.

### Serve published values at every level

Kreis, Land and Germany values are the BA's own rows, not sums of Gemeinden:
the sums would miss every suppressed value. A Kreis with a single Gemeinde
(kreisfreie Stadt, Berlin, Hamburg) has no Kreis row in the file; its
Gemeinde row is the Kreis value (verified: all 106 such Kreise have exactly
one Gemeinde row). This differs from the temperature theme, where Kreis and
Land values are computed roll-ups (ADR 015), because here the source
publishes the totals.

### Territorial differences stay visible, not mapped

The 38 VG250 Gemeinden without a BA row get `not_reported`
(`no_source_record`); the four BA Gemeinden without a VG250 counterpart stay
in raw only. Both lists are pinned in the contract for the 2025 vintage; the
key-coverage check fails on any difference not listed there. No old-to-new
mapping is built (decision of 14 Nov 2026): it would need a change list the
BA file does not contain. Known consequence: a Gemeinde that absorbed another
one after 2022 and kept its key shows the 2025 value of the larger area on
its 2022 outline.

### The map's ratio is marked as an own calculation

The map shows employees at place of work divided by employees at place of
residence (`jobs_ratio`). Both come from the same file and date, so no 2022
census population is combined with 2025 employment. The ratio is computed in
the serving view, never stored as a source value, and labelled "eigene
Berechnung" on the page. A ratio exists only where both values are published.

### A structural contract instead of a checksum for future vintages

VG250 and Zensus are pinned by checksum because they do not change. The BA
file is replaced every year, and the pipeline should load the next one
without a code change. The contract therefore pins the structure (sheets,
column numbers, the reference date in the imprint, key formats, value signs,
16 Länder, one Germany and one unassigned row) for any vintage, and the
checksum, row counts and territorial differences only for the known 2025
vintage. A daily sensor (`ba_gemeindedaten_release_sensor`, off by default)
sends one HEAD request for the next vintage and starts `germany_employment`
when it appears. For an unknown vintage the key-coverage check fails until
its territorial changes are reviewed and added to the contract.

## Consequences

- Checks against the source, all passing for 2025: Gemeinden sum exactly to
  their Kreis where nothing is suppressed and never exceed it where something
  is; Kreise sum exactly to their Land and the Länder plus the BA's
  "keine Angabe/keine Zuordnung möglich" row exactly to Germany, for the ten
  additive indicators; four identities between columns hold in every row.
  The unassigned row (2,160 employees at place of work) explains the gap
  between the Länder and the Germany total that the source survey had left
  open.
- 242 Gemeinden have no BA values (204 unincorporated areas, 38 merged), and
  1,261 Gemeinden have no ratio because a value is suppressed. Kreise, Länder
  and Germany are complete.
- Commuter columns and "Wohnort gleich Arbeitsort" do not add up across
  levels (a commuter between two Gemeinden of a Kreis is not a Kreis
  commuter); the catalogue marks them non-additive and the totals check skips
  them. At Germany level the BA reports them as 0; the page does not show
  them there.
- The model costs one generic table instead of per-theme columns; queries
  that need several indicators pivot (as the serving view does).
- New dependency: `openpyxl` to read the xlsx.
