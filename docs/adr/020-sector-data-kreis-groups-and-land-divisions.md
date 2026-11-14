# ADR 020: Sector data from two BA products, Kreis groups and Land divisions

Status: Accepted on the `germany-sectors` branch (14 Nov 2026).

## Context

The question to answer is how many jobs in a region depend on the motor
vehicle industry, mechanical engineering, chemicals and steel (WZ 2008
divisions 29, 28, 20 and group 24.1). The source survey and the files
checked on 14 Nov 2026 show:

- No source without a login has sector figures per Gemeinde.
- The BA *Regionalreport über Beschäftigte* (product 709) is published per
  Kreis, one xlsx per Kreis and quarter, each with five quarters. Its table
  2.3 has the WZ sections and three groups of manufacturing: "Metall- und
  Elektroindustrie sowie Stahlindustrie" (divisions 24–30, 32, 33),
  "Vorleistungsgüter, insbesondere chemische Erzeugnisse und
  Kunststoffwaren" (16, 17, 19, 20, 22, 23) and "überwiegend häuslich
  konsumierte Güter" (10–15, 18, 21, 31). The four industries cannot be
  separated at Kreis level.
- The BA *Beschäftigte nach Wirtschaftszweigen* booklets are published per
  Land and for Germany with every division and group, so the four industries
  are there exactly, but only at Land level.
- Both count employees subject to social insurance at place of work: the
  Regionalreport's Kreis totals for 30 June 2025 equal the Gemeindedaten's
  employees at place of work exactly (ADR 019).
- Both suppress small values with `*`, as the Gemeindedaten do.

## Decision

### Two sources, each at the level it publishes

Kreise come from the Regionalreport (sections and the three groups as
published); Länder and Germany from the WZ booklets (the total, every
division, group 24.1 and "keine Angabe"). Neither is extended to a level it
does not publish. ADR 019 is refined accordingly: a source writes rows in
`normalized.de_indicator_value` only for the levels it publishes, and within
such a level for every region, with an explicit status. Indicators a level
does not have are reported as `not_published_at_level` by the serving view,
not as missing values.

### Land groups as declared sums, from one of two routes

So that Kreise and Länder can be compared, the three groups and
manufacturing as a whole are also given for Länder and Germany. Neither
product publishes them per Land, so they are own calculations, labelled as
such, with the route stated per value (`<group>_basis`):

1. `sum_of_divisions`: the sum of the Land's published WZ divisions of the
   group. Which divisions make up which group is data, not code:
   `normalized.de_indicator_sum_component` lists the components of each
   derived indicator (`wz_sum_*`).
2. `sum_of_kreise`: if a division is suppressed, the sum of the Land's Kreis
   values from the Regionalreport, if all of them are published and no
   quality note exists for the Kreise of that Land and quarter.

Both routes agree exactly wherever both are complete: 37 of 37 comparisons
for the five quarters of the March 2026 files, apart from the documented
Hessen quarter below, whose Kreis values are incomplete. A sum is formed only from
complete parts; otherwise the value is `suppressed`. With route 1 alone the
share of metal, electrical and steel would be missing for Hamburg, Bremen and
the Saarland, where single divisions are suppressed; with route 2 all 16
Länder have it.

### Suppressed values are a value type, not an error

`suppressed` is one of the statuses every value carries, from raw (`*`
kept as text) to the page (its own grey, its own text "von der BA
geheimgehalten", and for derived values "nicht berechenbar: Teilwerte
geheimgehalten"). Checks treat a suppressed part as a bound, not as zero: a
published total must be at least the sum of the visible parts.

### Germany includes employees not assignable to a Land

In both products the Germany total exceeds the sum of the Länder by about
2,000 employees per quarter (2,285 on 31 Mar 2025, 2,140 on 31 Mar 2026), in
the total and in a few divisions. The Gemeindedaten show the same as a row
"keine Angabe/keine Zuordnung möglich": on 30 Jun 2025 the gap in the
Regionalreport is 2,160, exactly that row. The check therefore requires
Germany ≥ Σ Länder, the gap at most 0.01 % of Germany, a division's gap at
most the total gap, and the gap equal to the Gemeindedaten's row where both
have the date.

### Reviewed source discrepancies become quality notes

The check found one difference in the BA's own figures: in the files for
31 Mar 2026, Main-Kinzig-Kreis falls from 142,459 to 93,730 employees in one
quarter (-34 %), in every section and also by place of residence, while
Hessen barely changes; the Kreise of Hessen fall 47,649 short of the Land.
No other Kreis changes by more than 10 %. The values are published as they
are (the terms of use forbid altering them), but not silently:

- the contract lists the discrepancy, and the check accepts exactly this
  gap for this Land and quarter; any other gap fails it;
- `normalized.de_indicator_quality_note` carries a note for the Kreis and
  its Land; the Kreis sum of that Land is not used as a route for Land
  groups; the map shows the note with the Kreis ("Hinweis zur
  Datenqualität").

A later file that revises the quarter replaces the values (next section);
the note then no longer applies to the newest quarter.

### Quarters and revisions

Every quarter in every file is loaded; when a quarter appears in several
files, the newest file is used. All downloaded files are kept on disk.
The refresh rebuilds all sector values from raw, so a revised quarter
replaces the earlier one.

### Considerate acquisition

A quarter means 434 downloads (400 Kreis files and 17 booklets for the
quarter and for the same quarter a year earlier). They are sent one at a
time with a pause of 3 s (about 22 minutes), never repeated for a file on
disk, and capped at 600 requests per run. Every file is checked against the
structural contract before it is kept; a single bad file stops the run
instead of skipping a Kreis. A daily sensor (off by default) sends two HEAD
requests for the next quarter.

The BA limits the volume of requests, not only their pace. On 14 Nov 2026 a
first download of 434 files needed no retries; a second one an hour later from
the maintainer's machine (most likely the same network) received HTTP 429
at file 262 of 400, without a
`Retry-After` header, and still did after waits of 10, 30 and 90 s. HTTP 429
is therefore treated as "come back later", not as an error to retry soon:

- network errors and 5xx: retries after 10, 30 and 90 s;
- 429: retries after the server's `Retry-After`, or after 1, 5 and 15
  minutes;
- if the server still refuses, the client records a cool-down of at least
  one hour in `data/raw/germany/ba_rate_limit.json`. Every client (both raw
  assets, the release sensor, the next run) checks it before sending
  anything, so no second step starts asking once the server has throttled.
  The run stops; `run_sectors` says how many Kreis files are on disk and when
  to run again (exit status 2), and the next run continues with the missing
  files.

### Map value

The map shows the share of employees in the "Metall-, Elektro- und
Stahlindustrie" group at Kreis and Land level, on a fixed scale from 0 to
40 %. It is the only group published at both levels that contains three of
the four industries. Gemeinden are not offered for this theme.

## Consequences

- At Kreis level the four industries are reported only inside their groups:
  motor vehicles, mechanical engineering and steel within "Metall-, Elektro-
  und Stahlindustrie", which also holds metal products, electronics,
  electrical equipment and other vehicles; chemicals within
  "Vorleistungsgüter", which also holds paper, glass and plastics. The page
  and the docs say so.
- Land and Kreis use different groupings in the sources; the declared sums
  make them comparable and the check shows whether they agree.
- The BA hides the metal, electrical and steel group for three Kreise
  (Wolfsburg, Ingolstadt, Passau on 31 Mar 2026), presumably because one
  employer dominates; they are grey on the map with "von der BA
  geheimgehalten", not estimated.
- No Gemeinde level for this theme.
- About 210 MB of xlsx per quarter stay on disk (decision of 14 Nov 2026:
  keep all files).
