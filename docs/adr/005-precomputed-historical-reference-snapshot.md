# ADR 005: Distribute the historical reference, not the historical backfill

Status: Accepted.

## Context

Rebuilding the 1995–2025 HOSTRADA reference requires 1,116 source downloads,
approximately 220 GB of transferred data, and about 147 million intermediate
PLR-hour observations. None of those intermediates is needed to serve an
hourly forecast after the reference statistics exist.

## Decision

Distribute a checksummed PostgreSQL data-only archive containing exactly the
PLR and Berlin hourly reference tables. Verify the archive, installed PLR
fingerprint, complete calendar coverage, sample counts, and ordered statistics
before considering an operational installation ready.

Keep original-source reconstruction as a documented optional maintenance
workflow. Its source-manifest and hourly-observation checks remain separate
from source-independent operational snapshot validation.

## Consequences

A validated clean-room operational bootstrap takes approximately ten minutes
instead of requiring the entire historical backfill. The current archive is
231,645,982 bytes and remains outside Git. Changing reference years or PLR
boundaries requires publishing a compatible replacement archive and manifest.
