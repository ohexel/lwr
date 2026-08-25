# ADR 002: Use current Berlin PLRs and area-weighted temperature aggregation

Status: Accepted.

## Context

Operational users need neighborhood-scale temperature context. Forecast and
historical temperature values arrive on distinct source grids, while population is
published for Berlin planning areas (PLRs).

## Decision

Use the installed current PLR geography, containing 542 versioned areas, as the
shared analytical geography. Build separate forecast-grid-to-PLR and
HOSTRADA-grid-to-PLR spatial bridges. Weight intersecting source cells by
their overlap with each PLR instead of assigning conditions from a single
centroid or nearest grid point.

Validate the geography version and ordered PLR fingerprint before importing a
historical reference snapshot. Keep version identifiers inside the storage and
join contracts; do not repeat slowly changing geography metadata in every
hourly serving row.

## Consequences

Forecast and historical values are comparable through the same PLR boundaries,
not through equal source-grid identifiers. Changing the PLR geography requires
rebuilding dependent bridges and producing a compatible historical snapshot.
The operational forecast grid remains fully reproducible and takes several
minutes to initialize during a fresh installation.
