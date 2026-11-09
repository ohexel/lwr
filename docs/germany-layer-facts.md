# Germany layer: measured facts

Only values measured on this project's data and runs. Dates are measurement
dates. Nothing here is estimated.

## Sources (measured 2026-11-09)

| Fact | Value |
| --- | ---: |
| VG250 vintage | 31.12.2022 |
| VG250 GeoPackage archive | 72,214,199 bytes |
| VG250 delivered features (Staat / Länder / Kreise / Gemeinden) | 11 / 35 / 430 / 11,119 |
| Zensus 2022 table | 1000A-1003 |
| Zensus raw rows loaded (Länder / Kreise / Gemeinden extracts) | 204 / 4,812 / 129,444 |
| Zensus Gemeinden with a record | 10,786 |

## Normalized model (geography version `vg250-2022-12-31`)

| Fact | Value |
| --- | ---: |
| Länder | 16 |
| Kreise | 400 |
| Gemeinden (VG250 land parts, GF = 4) | 10,990 |
| of which unincorporated areas (*gemeindefreie Gebiete*) | 206 |
| unincorporated areas without a census record (population NULL) | 204 |
| unincorporated areas with census residents (Lohheide, Osterheide) | 2 |
| Total Gemeinde area, EPSG:3035 | 357,201 km² |
| Invalid geometries after transformation | 0 |
| Max. relative difference, Σ Gemeinde area vs. Kreis area | 5.0 × 10⁻¹⁵ |

## Source checks

| Check | Result |
| --- | --- |
| 1 Key coverage | pass; 204 non-blocking exceptions (unincorporated areas), 0 blocking |
| 2 Hierarchy integrity | pass; 0 violations |
| Σ Gemeinde population per Kreis = published Kreis total | 400 of 400 Kreise exact |
| Σ Gemeinden = Σ Kreise = Σ Länder | 82,711,282 |
| Published Germany total | 82,719,540 (+8,258 vs. Σ Länder, unexplained) |

## Runtimes (sandbox → host PostgreSQL 16.9 / PostGIS 3.5.2)

| Step | Duration |
| --- | ---: |
| Raw load, VG250 (4 levels) + Zensus (3 extracts) | 10.4 s wall |
| `refresh_de_hierarchy` | 11.4 s |
| `refresh_de_population` | 1.0 s |
