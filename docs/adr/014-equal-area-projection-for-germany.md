# ADR 014: Equal-area projection for Germany-wide area weights

Status: Accepted on the `germany-layer` branch (14 Nov 2026).

## Context

The Berlin pipeline weights ICON-D2 grid cells by the area they share with each
planning area, computed in EPSG:25833 (ETRS89 / UTM zone 33N). That works for one
city. Germany spans two UTM zones (32 and 33), and the BKG VG250 boundaries
are delivered in EPSG:25832 (zone 32N). A single UTM zone distorts areas
towards its edges, and the weights here are area ratios.

## Decision

- Germany-wide geometry for the Gemeinde, Kreis and Land tables and for the
  ICON grid cells is stored in **EPSG:3035** (ETRS89-LAEA, an equal-area
  projection). All intersection areas and weights are computed there.
- The Germany grid cells are not rebuilt from the grid file. They are the
  Berlin pipeline's cell polygons (`normalized.icon_cell`, EPSG:25833), read
  only and transformed with `ST_Transform` to EPSG:3035, kept where they
  intersect a Gemeinde.
- Berlin's own bridges stay in EPSG:25833 and are not touched.

## Alternatives considered

- **EPSG:25832 or 25833 for all of Germany.** One projection, no transform,
  but areas are not preserved, so weights would carry a distortion that depends
  on where a Gemeinde lies.
- **One bridge per UTM zone.** Preserves local accuracy but needs a rule for
  cells and Gemeinden that straddle the zone boundary, and two sets of tables.
- **Rebuilding each cell from the grid's longitude/latitude vertices.** This
  was the first implementation. It was replaced because it gave no measurable
  benefit over transforming the existing polygons and needed about 50 lines of
  the densest SQL in the layer.

## Consequences

Measured on the installed database (10,990 Gemeinden, 82,381 cells,
184,464 bridge rows):

- The Gemeinde areas sum to 357,201 km². The largest relative difference
  between the sum of a Kreis's Gemeinde areas and the Kreis area is
  5.0 × 10⁻¹⁵ (`docs/germany-layer-facts.md`).
- Check 4: for every Gemeinde the sum of intersection areas divided by its
  area lies between 0.9999999999998317 and 1.0000000000001266. The check
  requires at least 0.999.
- Check 7 compares Berlin as one Gemeinde (Germany bridge, EPSG:3035) with the
  area-weighted roll-up of the 542 planning areas (Berlin bridge, EPSG:25833)
  from the same cell values. For the run of 2026-11-14 09:00 UTC the largest
  difference over 25 lead hours is 0.00090 °C (temperature) and 0.00086 °C
  (apparent temperature); the tolerance is 0.01 °C. Two bridges built in
  different projections therefore agree closely.
- Replacing the vertex rebuild by `ST_Transform` was compared with the result
  it replaced: the cell and bridge row sets were identical; the largest
  absolute difference in `fraction_of_gemeinde` is 4.1 × 10⁻¹², and in cell
  area 4.9 × 10⁻¹² relative. Relative differences of intersection area reach
  1.6 × 10⁻⁷ only for slivers of 0.0026 m²; for intersections of 1,000 m² or
  more the largest is 5.7 × 10⁻¹⁰. A review comparison before the change gave
  a largest distance of 1.2 × 10⁻⁸ m between the two cell shapes.
- Rebuilding the cells and bridge takes 126 s on an installed database. The
  bridge needs an index on its foreign key to the cell table
  (`de_icon_cell_gemeinde_bridge_cell_idx`); without it the same rebuild took
  648 s.
- Germany tables and Berlin tables use different projections. Any query that
  joins them has to transform one side; the cross-layer check does this by
  comparing values, not geometries.
