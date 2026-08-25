"""Shared, bounded retention rules for operational forecast data."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


FORECAST_RETENTION_HOURS_ENV = "FORECAST_RETENTION_HOURS"
DEFAULT_FORECAST_RETENTION_HOURS = 24
MAX_FORECAST_RETENTION_HOURS = 24


def forecast_retention_hours(value: int | None = None) -> int:
    """Resolve the configured rolling window and enforce its hard limit."""
    if value is None:
        raw_value = os.getenv(
            FORECAST_RETENTION_HOURS_ENV,
            str(DEFAULT_FORECAST_RETENTION_HOURS),
        )
        try:
            hours = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"{FORECAST_RETENTION_HOURS_ENV} must be an integer "
                f"between 1 and {MAX_FORECAST_RETENTION_HOURS}"
            ) from exc
    else:
        hours = value

    if isinstance(hours, bool) or not isinstance(hours, int):
        raise ValueError(
            f"Forecast retention must be a whole number between 1 "
            f"and {MAX_FORECAST_RETENTION_HOURS} hours"
        )
    if not 1 <= hours <= MAX_FORECAST_RETENTION_HOURS:
        raise ValueError(
            f"Forecast retention must be between 1 and "
            f"{MAX_FORECAST_RETENTION_HOURS} hours"
        )

    return hours


def forecast_retention_cutoff(
    *,
    now_utc: datetime | None = None,
    retention_hours: int | None = None,
) -> datetime:
    """Return the UTC instant before which forecast runs must be removed."""
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Forecast retention requires a timezone-aware timestamp")

    return now.astimezone(timezone.utc) - timedelta(
        hours=forecast_retention_hours(retention_hours)
    )


def forecast_partition_window_start(
    *,
    now_utc: datetime | None = None,
    retention_hours: int | None = None,
) -> datetime:
    """Return the oldest whole-hour run inside the configured rolling window."""
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Forecast partitions require a timezone-aware timestamp")

    current_hour = now.astimezone(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return current_hour - timedelta(hours=forecast_retention_hours(retention_hours) - 1)
