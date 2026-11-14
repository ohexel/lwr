# ADR 015: Roll-up weighting for Kreis, Land and Bund

Status: Accepted on the `germany-layer` branch (14 Nov 2026).

## Context

The forecast is computed per Gemeinde by area-weighting grid cells. Kreis, Land
and Bund values are rolled up from Gemeinden, and there is more than one
defensible weight. A mean over the territory answers "what is the weather
over this region". A mean over residents answers "what does the average
resident experience", and these differ where people live in different places
from where the land is.

## Decision

Serve both, side by side, for Kreis, Land and Bund:

- **Area-weighted:** Gemeinde values weighted by Gemeinde area. Because the
  Gemeinden of VG250 partition their Kreis (the largest relative difference
  between the sum of Gemeinde areas and the Kreis area is 5.0 × 10⁻¹⁵), this
  equals weighting the grid cells directly by area within the Kreis.
- **Population-weighted:** Gemeinde values weighted by Zensus 2022 total
  population. A Gemeinde without a census record (population `NULL`, for
  example most unincorporated areas) contributes no weight. Its population is
  never treated as 0 residents; the Kreis total is the sum over Gemeinden that
  have a value.

The map offers the choice as a switch and shows the other value in the
details panel. The serving views contain measurements only, without classes or
scores (the README's "Scope and data interpretation").

## Alternatives considered

- **Area weighting only.** Simplest, but hides a difference that matters for
  a regional atlas.
- **Population weighting only.** Overstates cities for questions about land
  and infrastructure.
- **A single configurable weight.** Would force each consumer to pick, and the
  views would no longer show the choice.

## Consequences

Each roll-up view carries four value columns instead of two (temperature and
apparent temperature, each by area and by population), and the three views
(`de_current_{kreis,land,bund}_forecast_25h`) repeat the same pattern. The
24-hour summary views (`de_current_{kreis,land,bund}_summary`) follow the same
four columns, once for the maximum and once for the minimum (added
2026-11-14).

The difference is real but varies with the weather. Measured examples:

- Germany, lead hour 0 of the run of 2026-11-09 16:00 UTC, air temperature:
  5.25 °C area-weighted against 6.08 °C population-weighted
  (`docs/germany-layer-facts.md`).
- Germany, lead hour 0 of the run of 2026-11-14 09:00 UTC: 2.81 °C against
  2.83 °C.
- Largest Kreis difference in the apparent temperature, any lead hour, run of
  2026-11-14 09:00 UTC: 2.35 °C (Reutlingen, lead hour 5).

The roll-up identity holds exactly up to floating-point summation order: the
Länder area-weighted values re-aggregated to Germany equal the Bund value
(acceptance test, tolerance 1 × 10⁻⁹ °C), and the sum of Gemeinde population
equals the sum over Kreise and Länder, 82,711,282.
