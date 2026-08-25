# ADR 004: Preserve population quality failures instead of inventing values

Status: Accepted.

## Context

Population source records may contain malformed values, inconsistent totals, or
other quality failures. Downstream temperature rows must remain available even when
a neighborhood's population cannot be trusted.

## Decision

Load the original source record, validate it through explicit database quality
gates, and retain rejected records separately. Join temperature observations to population
without removing rejected PLRs. Expose `population_status` alongside nullable
population counts in the serving view.

The checked-in, checksummed population CSV is an acquisition fallback for the
known published 2025-12-31 source. It is not a fabricated replacement dataset.

## Consequences

The verified current input contains 542 records: 540 accepted and two
rejected. Missing population remains distinct from zero residents, and the
temperature observation survives rejection. Analysts can decide whether rejected
areas should be omitted, investigated, or treated through a separate policy.
