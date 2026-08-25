# ADR 007: Serve observations and reference facts, not analyst decisions

Status: Accepted.

## Context

Forecasts, neighborhood historical references, citywide historical references,
and population counts can support many analytical interpretations. Thresholds,
exposure estimates, and risk classifications are not yet agreed business
requirements.

## Decision

Expose one 23-column PLR-level temperature-context contract containing geographic
identity, forecast timing, current observations, six PLR reference values, six
Berlin reference values, and population availability.

Include the official PLR display name solely as an analyst-facing convenience.
The stable PLR identifier remains the engineering key; names may repeat and
are never used for population, spatial, or historical-reference matching.

Do not calculate exceedance flags, forecast-minus-reference differences,
population exposure, vulnerability scores, or heat-risk categories. Do not
repeat geography version, population reference date, source-grid identifiers,
quality sample counts, or historical provenance in hourly serving rows.

## Consequences

Analysts receive stable, joinable facts while retaining ownership of business
interpretation. Slowly changing versions and provenance remain available in
their operational source and reference tables. The pipeline can support a
future targeted exposure demonstration without making it part of the core
serving contract.
