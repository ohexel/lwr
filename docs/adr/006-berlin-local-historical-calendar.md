# ADR 006: Group historical references by the Berlin-local calendar

Status: Accepted.

## Context

Hourly temperature forecasts are interpreted in local Berlin time, while
historical source observations are identified by UTC timestamps. Daylight
saving, leap years, and reference-period boundaries make naive fixed-count
calendar grouping incorrect.

## Decision

Convert source timestamps using the `Europe/Berlin` timezone database and group
by local month, day, and hour. Include local years 1995–2025 and exclude local
February 29. Preserve distinct UTC observations when a local hour repeats and
do not synthesize missing spring-transition hours.

Store median, continuous p90, observed maximum, and sample counts for both air
temperature and apparent shade temperature. Validate expected sample counts
from the historical timezone rules rather than assuming every calendar hour
contains exactly 31 observations.

## Consequences

The complete reference contains 8,760 Berlin rows and 4,747,920 PLR rows.
Included Berlin observations total 271,559, and sample counts range from 26 to
36. Local February 29 forecasts remain visible without invented historical
reference values.
