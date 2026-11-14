# ADR 016: Germany map: PMTiles outlines and a JSON values file

Status: Accepted on the `germany-layer` branch (14 Nov 2026).

## Context

The Germany map has to show a choropleth for 16 Länder, 400 Kreise and 10,990
Gemeinden, and the values change with every forecast run. The outlines do not
change between runs; the values do. The page is to be hosted as static files,
without a running service, database connection, basemap or API key (handover
decision D6). The Berlin map draws SVG (ADR 010), which suits its 542 planning
areas.

## Decision

Separate geometry from values:

- **Outlines** go into one **PMTiles** archive, built once per geography
  version from `normalized.de_*` with `tippecanoe` (zoom 4–10). Each feature
  carries only its regional key, in the property `key`.
- **Values** go into one small `values.json` per run, keyed by regional code,
  with descriptive field names and a `schema` number.
- **MapLibre GL JS** (4.7.1, pinned, with Subresource Integrity hashes) joins
  the two in the browser: the tile source sets `promoteId: "key"` and the page
  writes each value as feature state. Tiles are never rebuilt for a new run.
- The page has no basemap tiles and no API key. Boundaries are
  © GeoBasis-DE / BKG 2022 (Daten verändert), dl-de/by-2-0.

## Alternatives considered

- **GeoJSON per run.** No tile tool and no Range requests, but the geometry
  would be re-exported and re-downloaded with every run. A Kreis-only GeoJSON
  is the fallback named in the handover plan if a host cannot serve byte
  ranges; it is not implemented.
- **A tile server (vector tiles on request).** Best for dynamic data, but it
  is a service that has to be run and secured; the project is a portfolio
  piece on a local machine.
- **Values inside the tiles.** One download, but the tiles would have to be
  rebuilt (an export with a tile rebuild takes 41 s here) and re-uploaded for
  every run.

## Consequences

- The web host must answer **HTTP Range requests** for the `.pmtiles` file.
  Tested on www.ohexel.com and ohexel.com on 2026-11-09: `206 Partial Content`
  for first, middle and suffix ranges on a 232 MB file, with no content
  encoding. The host sends no CORS headers, so the tiles cannot be loaded from
  a page on another domain.
- Python's built-in web server cannot answer Range requests, so the local
  preview uses a small handler of its own (`src/germany/preview_server.py`).
- `tippecanoe` is needed once per geography version, not at run time.
- Measured on the export of the run 2026-11-14 09:00 UTC: tiles 13,139,513
  bytes (about 13 MB); `values.json` 3,511,495 bytes, 281,537 bytes gzipped.
  An export with a tile rebuild takes 41 s.
- Descriptive field names (for example
  `max_apparent_temperature_shade_population_weighted_c`) double the raw size
  of the values file (3.53 MB against 1.77 MB for the earlier one-letter keys,
  same run) but not the transfer size: the gzipped file is 289,187 bytes
  against 320,873. Readability was the reason for the change; the transfer
  cost did not rise.
- The 24-hour maxima shown on the map are read from the SQL summary views,
  not computed in the export, so analysts and the map use the same rule.
- Features of the first version were removed to keep the page explainable:
  there is no search field. Hover, click-to-pin (the only way to read a value
  on a touch screen), the level switch and the weighting switch remain.
- The colour scale was first fitted to each run (YlOrRd, warm colours only),
  so a cold day looked orange-red. Changed on 2026-11-14: a fixed
  cool-to-warm scale from -20 to +30 °C, the same on every day, so 0-10 °C
  is blue to pale blue and a colour always means the same temperature. No
  warning classes are drawn.
