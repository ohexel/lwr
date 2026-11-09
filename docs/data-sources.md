# Source systems, contracts, and attribution

The pipeline combines official Berlin geography and population data with DWD
temperature-forecast and climate products. Source licenses apply to source data and
redistributed derivatives; they do not automatically license this repository's
software.

## Berlin planning-area geometry

- Publisher: Amt für Statistik Berlin-Brandenburg.
- Dataset overview: [Lebensweltlich orientierte Räume in Berlin](https://daten.berlin.de/datensaetze/lebensweltlich-orientierte-raeume-in-berlin).
- Exact pipeline source: [Lebensweltlich orientierte Räume (LOR) (01.01.2021) - WFS](https://daten.berlin.de/datensaetze/lebensweltlich-orientierte-raume-lor-01-01-2021-wfs-34c86848).
- WFS endpoint: `https://gdi.berlin.de/services/wfs/lor_2021`.
- Geography used: 542 planning areas; source geometry status 2023-01-01.
- Working coordinate reference system: EPSG:25833.
- Published license for the exact WFS record: Creative Commons Attribution
  3.0 Germany, CC BY 3.0 DE. This source-specific catalogue record takes
  precedence over the overview page's generic
  [CC BY reference](https://opendefinition.org/licenses/cc-by/).
- Required attribution:

```text
Amt für Statistik Berlin-Brandenburg / Lebensweltlich orientierte Räume (LOR) (01.01.2021)
```

The current 542-PLR geography must not be silently combined with pre-2021 LOR
boundaries. Operational installation checks the exact geography version and
the sorted PLR identifier SHA-256 recorded in the HOSTRADA reference manifest.

### Official PLR display names

- Publisher and required attribution: Amt für Statistik Berlin-Brandenburg.
- Official directory: [Lebensweltlich orientierte Räume: Namensverzeichnis – 542 PLR](https://www.berlin.de/sen/stadt/stadtdaten/stadtwissen/sozialraumorientierte-planungsgrundlagen/lebensweltlich-orientierte-raeume/).
- Workbook: `lor_2021-01-01_k3_uebersicht_id_namen.xlsx`.
- Worksheet and columns: `LOR_2023_PLR`, `PLR_ID`, and `PLR_Name`.
- Size: 64,143 bytes.
- SHA-256: `86088afaa3ad8f163849256a037a9d304ff9930ca62ee475d7de37ed5e01dc08`.
- Published page license: Creative Commons Attribution 3.0.
- Verified repository fallback:
  `resources/static/plr_names/lor_2021-01-01_k3_uebersicht_id_namen.xlsx`.

The advertised direct link serves a genuine XLSX workbook. Unlike the AfS
population catalogue link, it does not require an alternative download host.
The loader validates all 542 identifiers against the HOSTRADA geography
fingerprint and preserves leading zeroes, German characters, and duplicate
neighborhood names. Names are joined only into the final analytical serving
views; they are never used as engineering, population, or spatial join keys.

## Berlin population register

- Publisher: Amt für Statistik Berlin-Brandenburg.
- Dataset: [Einwohnerinnen und Einwohner in Berlin in LOR-Planungsräumen am 31.12.2025](https://daten.berlin.de/datensaetze/einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025).
- Statistical universe: residents registered in Berlin with their main
  residence.
- Spatial grain: LOR planning area.
- Reference date: 2025-12-31.
- Published: 2026-04-02.
- Published license: Creative Commons Attribution (`cc-by`); the catalogue
  does not identify a more specific version, so none is assumed here.
- Required attribution:

```text
Amt für Statistik Berlin-Brandenburg
```

Required CSV columns are `RAUMID`, `E_E`, `E_E65U80`, `E_E80U110`, and
`ZEIT`. The accepted age-65-plus count is the sum of the 65–79 and 80-plus
source fields. Invalid population rows are retained in an explicit rejection
table rather than interpreted as zero residents.

### CKAN catalogue versus downloadable CSV

Berlin's catalogue metadata is available through the CKAN `package_show` API:

```text
https://datenregister.berlin.de/api/3/action/package_show
```

The dataset identifier is:

```text
einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025
```

For example:

```bash
curl -G \
  'https://datenregister.berlin.de/api/3/action/package_show' \
  --data-urlencode \
  'id=einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025'
```

**Important:** catalogue discovery did not produce a reliably downloadable
CSV in this project. The advertised AfS resource URL,

```text
https://www.statistik-berlin-brandenburg.de/opendata/EWR_L21_202512E_Matrix.csv
```

returned an HTML site response rather than the CSV. The working source was the
provider's separate, opaque direct-download URL:

```text
https://download.statistik-berlin-brandenburg.de/c9771e49e9b212b3/f6a9c1df6163/EWR_L21_202512E_Matrix.csv
```

`src/download_afs_population.py` therefore uses CKAN for catalogue metadata,
not for a guaranteed file location. It deliberately hard-codes the observed
working direct URL and records that choice in acquisition metadata. Provider
path components may change without the catalogue record disappearing.

The exact verified source CSV is also distributed inside the repository at:

```text
resources/static/population/2025-12-31/EWR_L21_202512E_Matrix.csv
```

Expected SHA-256:

```text
ce1ed8d1e31c4c2064a0ed9322d2d78432fa8e928cfeabce106d81a724901c43
```

If catalogue access or direct download fails, the downloader validates and
restores this small fallback. A replacement population release requires an
explicit source update, reference-date review, quality validation, and an
assessment of compatibility with the installed planning-area geography.

## DWD ICON forecast grid

- Publisher: Deutscher Wetterdienst.
- Grid: `icon_grid_0047_R19B07_L`.
- Source:
  `https://opendata.dwd.de/weather/lib/cdo/icon_grid_0047_R19B07_L.nc.bz2`.
- Compressed source size: 201,429,184 bytes.
- SHA-256:
  `985ae3f69611fa224c5506417520f6291d14f3135a73d5e88901a24e99dc6648`.
- Vertices: 272,089.
- Cells: 542,040.
- Cell-to-vertex topology rows: 1,626,120.

The complete model grid is initialized once. PostgreSQL then stores a
Berlin-specific spatial bridge and forecast mask; hourly temperature processing
does not retain nationwide decoded cell values in PostgreSQL.

DWD's [Open Data FAQ](https://www.dwd.de/DE/leistungen/opendata/faqs_opendata.html)
states that DWD geodata may be reused under CC BY 4.0 with source attribution.
No separate, grid-file-specific license statement was located for
`icon_grid_0047_R19B07_L.nc.bz2`. This project therefore assumes that the grid,
which DWD distributes as supporting ICON model data, is covered by the same CC
BY 4.0 terms as the associated forecast geodata. That is an explicit project
assumption rather than a confirmed product-specific statement. Attribute the
source to Deutscher Wetterdienst and recheck the assumption before publicly
redistributing the optional static archive.

## DWD ICON-D2-RUC forecast fields

- Publisher: Deutscher Wetterdienst.
- Source root:
  `https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p`.
- Indicators: `T_2M`, `RELHUM_2M`, `U_10M`, and `V_10M`.
- Forecast identity: UTC model run plus lead time.
- File format: GRIB2.
- Availability: rolling publication and limited upstream retention.

The pipeline computes wind speed from its two horizontal components and uses
temperature, humidity, and wind to calculate shade apparent temperature.
Solar radiation is not included, so this measure is not presented as a full
heat-stress assessment.

The project applies the DWD Open Data FAQ's general CC BY 4.0 geodata terms to
these forecast fields. Attribute the source to Deutscher Wetterdienst and
recheck the current DWD terms before redistributing raw forecast files.

## DWD HOSTRADA climate history

- Publisher: Deutscher Wetterdienst, Climate Data Center.
- Dataset: HOSTRADA version 1.0, high-resolution hourly gridded temperature,
  humidity, and wind data.
- Source root:
  `https://opendata.dwd.de/climate_environment/CDC/grids_germany/hourly/hostrada`.
- Source grid: approximately 1 km, EPSG:3034.
- Reference years: 1995–2025.
- Variables: `tas`, `hurs`, and `sfcWind`.
- Source format: monthly NetCDF files.
- Applicable CDC terms:
  [DWD Climate Data Center OpenData terms of use](https://opendata.dwd.de/climate_environment/CDC/Terms_of_use.pdf).
- Published license for the CDC OpenData area: Creative Commons Attribution
  4.0 International, CC BY 4.0.

Recommended source attribution:

```text
Deutscher Wetterdienst (DWD), Climate Data Center (CDC), HOSTRADA v1.0;
derived Berlin planning-area and Berlin-wide hourly reference statistics,
1995–2025.
```

The distributed reference is derived data, not the original nationwide NetCDF
archive. Preserve the original source attribution, identify the reference as
modified/aggregated, link the applicable license, and retain the Berlin LOR
geography attribution wherever that derived reference is redistributed.

## Germany layer: BKG VG250 administrative boundaries

- Publisher: Bundesamt für Kartographie und Geodäsie (BKG).
- Product: Verwaltungsgebiete 1:250 000, levels ("Ebenen"), GeoPackage,
  UTM32s (EPSG:25832).
- Vintage: **Stand 31.12.2022** (`aktualitaet.txt` in the archive).
- Source:
  `https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_1231/2022/vg250_12-31.utm32s.gpkg.ebenen.zip`.
- Archive size: 72,214,199 bytes.
- Archive SHA-256:
  `4a4d9590cac21028c3f120172d1ab3b026ac0a8b202d0f168e1304383ae44e59`.
- Extracted GeoPackage (`DE_VG250.gpkg`) SHA-256, for information:
  `12f0a8d27b40ed116e67c881cadc69dba4c5c6ad635df751305d18d3e9141144`.
- Retrieval: `uv run --env-file .env python -m src.germany.acquire --only vg250`
  (verifies size and checksum, refuses to overwrite a different local file).
- Loaded into: `raw.de_vg250`, one row per delivered feature, column `level`
  in `sta`, `lan`, `krs`, `gem`.
- License: Datenlizenz Deutschland – Namensnennung – Version 2.0
  (dl-de/by-2-0), as stated in the archive's
  `quellenvermerk_datenlizenz_deutschland.pdf`.
- Required attribution (BKG *Quellenvermerk*; link it to
  <https://www.bkg.bund.de> on web pages; this project modifies the data):

```text
© GeoBasis-DE / BKG 2022 (Daten verändert)
```

**Why 31.12.2022.** Zensus 2022 Gemeinde results use the territorial status
(*Gebietsstand*) of 15.05.2022. BKG publishes VG250 only for 01.01. and 31.12.
Comparing keys on 2026-11-09:

| VG250 vintage | Gemeinden (GF = 4) | Zensus keys missing in VG250 | VG250 keys missing in Zensus |
| --- | ---: | ---: | ---: |
| 01.01.2022 | 10,993 | 2 (Pinnow, Schwedt/Oder) | 209 |
| 31.12.2022 | 10,990 | 0 | 204, all *Gemeindefreies Gebiet* |

With 31.12.2022 every Zensus Gemeinde (12-digit ARS) has exactly one geometry.
The 204 unincorporated areas keep their geometry; their population is NULL,
never 0. Residual risk: boundary changes between 15.05. and 31.12.2022 that
kept all keys would not be detected by a key comparison.

**Geofactor.** The VG250 documentation (`dokumentation/vg250.pdf`, attribute
GF) defines 1 = water without structure, 2 = water with structure, 3 = land
without structure, 4 = land with structure, and states that each
administrative unit has exactly one GF = 4 record; filtering to GF = 4
removes the North Sea, Baltic Sea and Lake Constance parts. Raw keeps all
records; normalization filters to GF = 4.

| Level | Delivered features | GF = 4 |
| --- | ---: | ---: |
| Staat (`sta`) | 11 | 1 |
| Länder (`lan`) | 35 | 16 |
| Kreise (`krs`) | 430 | 400 |
| Gemeinden (`gem`) | 11,119 | 10,990 |

## Germany layer: Zensus 2022 population

- Publisher: Statistische Ämter des Bundes und der Länder.
- Database: Zensusdatenbank, <https://ergebnisse.zensus2022.de/>.
- Table: **1000A-1003** "Personen: Alter (5 Altersklassen)", statistic 1000A
  "Bevölkerung kompakt (Gebietsstand 15.05.2022)".
- Reference date (*Stichtag*): 2022-05-15. Gebietsstand: 15.05.2022.
- Measure used by the Germany layer: **total population** per region, the
  row without an age class. The five age classes (`ALTKL1`: under 18,
  18–29, 30–49, 50–64, 65 and older) are loaded into raw as delivered but are
  not served.
- Retrieval: Zensus REST API (`/api/rest/2020/data/tablefile`, format
  `ffcsv`), one request per regional variable. Each file also contains the
  Germany (`GEODL1`) rows.

| Regional variable | Level | Regions | Flat CSV SHA-256 |
| --- | --- | ---: | --- |
| `GEOBL1` | Länder | 16 | `83b6ea9532e74e4c27327e9d6544e4abd48923dc1ae11563225d6acd312674f3` |
| `GEOLK4` | Kreise (Stand 15.05.22) | 400 | `71946b5fcc69a3e29d5732e33231badd7a19a7922e7d9bc39efb82d2e868f35f` |
| `GEOGM4` | Gemeinden (Gebietsstand 15.05.2022) | 10,786 | `e3489be7b6c1d432f155788f9c64ea84e3f94e535891fa2582bbd81c5c681d4a` |

The zip wrapper returned by the API differs on every request (its file name
carries a timestamp); the contained CSV was byte-identical across repeated
downloads, so the checksum applies to the extracted CSV.

**Access.** The API needs a free personal token (register at the Zensus
database, then copy it from the dialog "Webservice-Schnittstelle (API)").
Set it as `ZENSUS2022_TOKEN` in `.env`:

```bash
uv run --env-file .env python -m src.germany.acquire --only zensus
```

The token is sent only as an HTTP header. **The API echoes the token in plain
text in the `Username` field of its JSON responses**, so the acquisition code
redacts JSON bodies before they appear in errors, and never stores them. The
delivered table files contain no credentials. Never commit raw API responses.

- Loaded into: `raw.de_zensus2022_age`, all values kept as delivered text.
- License and attribution, as returned by the API's `Copyright` field and the
  table footer:

```text
© Statistische Ämter des Bundes und der Länder, Deutschland, 2026.
Lizenziert unter der Datenlizenz Deutschland - Namensnennung - Version 2.0
```

**Confidentiality method.** Zensus 2022 results are protected with the
**Cell Key Method**, a data-perturbing method ("stochastische Überlagerung").
Destatis states that, as a consequence, the values of a table row or column
do not necessarily add up to the published total, and that official
population counts of administrative units are published unchanged
([Destatis: Geheimhaltung mit der Cell-Key-Methode](https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Zensus2022/Datenbank/geheimhaltung-cell-key-methode.html);
[Destatis: Zensus 2022 Geheimhaltung](https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Zensus2022/Methoden/geheimhaltung.html)).
This is why the layer uses total population: the age breakdown is perturbed
(e.g. Gemeinde values for 65+ sum to 18,389,751 against a published Germany
value of 18,390,209), while total population is not. Observed on the loaded
files for total population:

| Comparison | Result |
| --- | --- |
| Σ Gemeinden per Kreis vs. published Kreis total | exact for all 400 Kreise |
| Σ Gemeinden, Σ Kreise, Σ Länder | 82,711,282 each |
| Published Germany (`GEODL1`) total | 82,719,540 (+8,258 vs. Σ Länder) |

The 8,258-person gap between the Germany total and the sum of the Länder is
in the source itself and is not yet explained; it must be resolved or
documented before the source-total check (M4) is finalised.

**Sign "-".** 42 Gemeinde age-class counts (34 Gemeinden) are delivered as
`-`; no total-population count is. The sign legend in Destatis' Zensus 2022
`Regionaltabelle_Bevoelkerung.xlsx` defines "– = Genau Null oder auf Null
geändert" (exactly zero, or changed to zero). No legend was found for the
database table itself; the project applies this definition and keeps the
original sign in raw.

## Combined attribution

When presenting the serving dataset or its downloadable derivatives, a concise
attribution can read:

```text
Forecast and climate data: Deutscher Wetterdienst (DWD), including HOSTRADA
v1.0 from the DWD Climate Data Center.

Planning-area geometry: Amt für Statistik Berlin-Brandenburg /
Lebensweltlich orientierte Räume (LOR) (01.01.2021).

Population data: Amt für Statistik Berlin-Brandenburg, Berlin residents at
their main residence, 2025-12-31.

Spatial aggregation and historical reference statistics were produced by this
project and are not original source publications.
```

For the Germany layer, add:

```text
Administrative boundaries: © GeoBasis-DE / BKG 2022 (Daten verändert),
dl-de/by-2-0.

Population: Zensus 2022, © Statistische Ämter des Bundes und der Länder,
Deutschland, 2026, dl-de/by-2-0.
```
