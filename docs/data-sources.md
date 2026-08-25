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
