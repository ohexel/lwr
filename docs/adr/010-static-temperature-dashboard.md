# ADR 010: Static, dependency-free temperature dashboard

Status: Accepted on the optional interactive-map feature branch.

## Context

The neighborhood-level forecast, summary, historical trajectories, PLR names,
and geometries already exist in PostgreSQL. A local demonstration and eventual
personal website need an interactive Berlin map without exposing database
credentials or introducing a continuously running application service. The
website otherwise consists primarily of static HTML.

## Decision

Export a complete static dashboard from PostgreSQL. Keep one compact map and
summary file, plus one plotting-detail file per PLR that the browser loads only
when needed. Simplify PLR boundaries in their metric EPSG:25833 projection and
draw the map and chart as SVG using plain local JavaScript and CSS.

Use hover for rapid exploration, click-to-pin for deliberate and touch-based
selection, and name/ID search plus roving keyboard focus for accessibility.
Expose forecast facts, historical observations and medians, and population
counts without deriving a heat-risk category.

## Consequences

The published website needs no map tiles, frontend dependency, CDN, API, or
database connection. Static hosts can cache detail files, and initial page load
does not transfer every historical trajectory. Each new forecast requires a
fresh export and upload. The generated data is intentionally excluded from Git,
while the exporter and presentation source remain versioned and testable.
