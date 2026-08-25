from __future__ import annotations

from datetime import datetime, timezone

import dagster as dg

from src.forecast_key import (
    ForecastKey,
    parse_lead_time,
)
from src.hostrada_contract import (
    HOSTRADA_MONTH_FORMAT,
    HostradaMonthKey,
)
from src.hostrada_reference import HOSTRADA_REFERENCE_CALENDAR_MONTHS
from src.retention.forecast_policy import forecast_partition_window_start


RUN_TIME_PARTITION_FORMAT = "%Y%m%dT%H%M"

# Dagster captures this bound when its code location loads. Reloading the
# location advances the visible window and removes expired historical runs.
WEATHER_HISTORY_START = forecast_partition_window_start()

# DWD ICON-D2-RUC supplies hourly leads 0-27. The forecast trajectory needs
# precisely leads 0-24: 25 observations spanning the next 24 hours.
FORECAST_HORIZON_MAX_LEAD_HOURS = 24
FORECAST_HORIZON_POINT_COUNT = FORECAST_HORIZON_MAX_LEAD_HOURS + 1
WEATHER_LEAD_TIMES = tuple(
    f"PT{lead_hour:03d}H00M"
    for lead_hour in range(FORECAST_HORIZON_POINT_COUNT)
)


RUN_TIME_PARTITIONS = dg.HourlyPartitionsDefinition(
    start_date=WEATHER_HISTORY_START,
    timezone="UTC",
    fmt=RUN_TIME_PARTITION_FORMAT,
    end_offset=1,
)

LEAD_TIME_PARTITIONS = dg.StaticPartitionsDefinition(
    WEATHER_LEAD_TIMES,
)

WEATHER_PARTITIONS = dg.MultiPartitionsDefinition(
    {
        "run_time": RUN_TIME_PARTITIONS,
        "lead_time": LEAD_TIME_PARTITIONS,
    }
)


# Source files are UTC calendar months. Leave the end open so diagnostic
# months outside the eventual 1995-2025 reference period remain materializable.
HOSTRADA_MONTHLY_PARTITIONS = dg.MonthlyPartitionsDefinition(
    start_date=datetime(1995, 1, 1, tzinfo=timezone.utc),
    timezone="UTC",
    fmt=HOSTRADA_MONTH_FORMAT,
)


# Reference partitions represent recurring Berlin-local calendar months.
HOSTRADA_REFERENCE_PARTITIONS = dg.StaticPartitionsDefinition(
    HOSTRADA_REFERENCE_CALENDAR_MONTHS,
)


def hostrada_month_from_partition(partition_key: str) -> HostradaMonthKey:
    return HostradaMonthKey.from_partition_key(partition_key)


def weather_partition_key(
    forecast: ForecastKey,
) -> dg.MultiPartitionKey:
    """
    Convert the domain ForecastKey into Dagster's two-dimensional
    partition key.

    This helper is intended to be reused by the future DWD sensor so
    sensor logic does not construct Dagster partition strings manually.
    """
    return dg.MultiPartitionKey(
        {
            "run_time": forecast.run_label,
            "lead_time": forecast.lead_time_label,
        }
    )


def forecast_key_from_partition(
    partition_key: dg.MultiPartitionKey,
) -> ForecastKey:
    """
    Convert a Dagster weather partition back into the domain ForecastKey.

    Assets should use this helper instead of parsing
    context.partition_key ad hoc.
    """
    dimensions = partition_key.keys_by_dimension

    try:
        run_time_label = dimensions["run_time"]
        lead_time_label = dimensions["lead_time"]
    except KeyError as exc:
        raise ValueError(
            "Weather partition must contain both "
            "'run_time' and 'lead_time' dimensions"
        ) from exc

    run_time = datetime.strptime(
        run_time_label,
        RUN_TIME_PARTITION_FORMAT,
    ).replace(tzinfo=timezone.utc)

    return ForecastKey(
        run_time=run_time,
        lead_time=parse_lead_time(
            lead_time_label
        ),
    )
