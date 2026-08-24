"""Small, explicit calendar contract for the fixed HOSTRADA reference."""

from __future__ import annotations


HOSTRADA_REFERENCE_START_YEAR = 1995
HOSTRADA_REFERENCE_END_YEAR = 2025
HOSTRADA_REFERENCE_CALENDAR_MONTHS = tuple(
    f"{calendar_month:02d}" for calendar_month in range(1, 13)
)


def hostrada_reference_month_from_partition(partition_key: str) -> int:
    """Reject ambiguous keys such as ``2`` or impossible months such as ``13``."""
    if partition_key not in HOSTRADA_REFERENCE_CALENDAR_MONTHS:
        raise ValueError(
            "HOSTRADA reference partition must be a calendar month "
            "between '01' and '12'"
        )

    return int(partition_key)
