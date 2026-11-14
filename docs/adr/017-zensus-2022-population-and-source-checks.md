# ADR 017: Zensus 2022 population and the checks against the source

Status: Accepted on the `germany-layer` branch (14 Nov 2026).

## Context

The Germany layer shows residents next to the forecast and weights the Kreis,
Land and Bund roll-ups by them (ADR 015). The source is the Zensus 2022
database. Two things needed a decision: which measure to serve, and what a
check against the source can honestly claim.

Zensus 2022 results are protected with the Cell Key Method, a perturbing
method. Destatis states that table rows and columns therefore do not
necessarily add up to the published total, and that official population counts
of administrative units are published unchanged
([Geheimhaltung mit der Cell-Key-Methode](https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Zensus2022/Datenbank/geheimhaltung-cell-key-methode.html);
[Zensus 2022 Geheimhaltung](https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Zensus2022/Methoden/geheimhaltung.html)).

## Decision

- **Serve total population**, not an age group. The source table is 1000A-1003
  ("Personen: Alter, 5 Altersklassen", statistic 1000A, Gebietsstand and
  reference date 15 May 2022), fetched through the Zensus API as flat CSV for
  Länder, Kreise and Gemeinden and pinned by SHA-256. The first plan was
  residents aged 65 and older; the age breakdown is perturbed (the Gemeinde
  values for 65+ sum to 18,389,751 against a published Germany value of
  18,390,209), while the total is not.
- **Keep the table as delivered.** `raw.de_zensus2022_population` holds all
  rows, including the five age classes, as text. The pipeline reads only the
  total (the row without an age class). Raw data stays unaltered; a later
  theme that needs an age class can use it without a second download. The table
  was called `de_zensus2022_age` until 14 Nov 2026; the new name says what the
  pipeline uses.
- **Test exact equality where the source promises it, and document the one
  known gap.** Check 3 (`de_source_totals`) compares the sum of Gemeinde
  values with the published value for every Kreis and every Land, and
  the published Germany total with the sum of Länder.
- **Keep a status for every population value.** `population_status` is one of
  `census_value`, `census_zero_or_set_to_zero`,
  `no_census_record_unincorporated`, `no_census_record` and
  `rejected_source_value`. A missing value is `NULL` with a status, never 0.
  Two of the five occur in the data. `census_zero_or_set_to_zero` is
  defensive: no Gemeinde total in the pinned extract is `-`, which the Zensus
  sign legend of the Destatis Regionaltabelle defines as "exactly zero, or
  changed to zero", but a future extract may contain it.

## Alternatives considered

- **65+ as planned.** Rejected for the reason above.
- **A tolerance in check 3.** The first plan assumed perturbation would
  require one. For totals the sums match exactly, so a tolerance would only
  hide a loading error.

## Consequences

Results on the loaded files (`docs/germany-layer-facts.md`):

- For all 400 Kreise the sum of Gemeinde population equals the published
  Kreis total, and for all 16 Länder the sum of Kreise equals the published
  Land total. The sum over Gemeinden, Kreise and Länder is 82,711,282.
- The published Germany total is 82,719,540, which is 8,258 more than the sum
  over Länder. The Destatis *Qualitätsbericht Zensus 2022* (20 Nov 2024)
  defines the population to include members of the armed forces, police
  and foreign service stationed abroad and their families (section 1.1) and
  says register-based results contain them "auf Bundesebene" (section 7.2).
  The report does not state their number. The gap is **consistent with** this
  definition; it is not confirmed by a published number. Check 3 expects
  exactly this difference for the pinned extract, so it fails if the source
  or the loading changes.
- Because the source files are pinned by checksum, check 3 cannot fail
  because the source changed. It tests that the pipeline neither loses nor
  duplicates residents between raw and normalized, and that the hierarchy
  adds up. It is described as that, not as a test of the source's quality.
- 10,786 of the 10,990 Gemeinden have a census record. 204 unincorporated
  areas (*gemeindefreie Gebiete*) have none, and their population is `NULL`;
  two (Lohheide and Osterheide) do have residents. All 204 are listed by
  check 1 as non-blocking exceptions.
- The Gebietsstand of VG250 (31 Dec 2022) was chosen because all 10,786 Zensus
  Gemeinde keys exist in it; the 1 Jan 2022 vintage lacks two. Boundary changes
  between 15 May and 31 Dec 2022 that kept their keys remain a possible source of error.
- Zensus data carry the licence dl-de/by-2-0 and the attribution
  "© Statistische Ämter des Bundes und der Länder, Deutschland, 2026".
