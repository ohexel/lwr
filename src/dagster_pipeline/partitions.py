from __future__ import annotations

from datetime import datetime, timezone

import dagster as dg

from src.forecast_key import (
    ForecastKey,
    parse_lead_time,
)


RUN_TIME_PARTITION_FORMAT = "%Y%m%dT%H%M"

# This tells Dagster the earliest canonical date that data could theoretically
# have. If we requested a partition with an earlier date, Dagster would not
# recognize it. This is independent from DWD ICON D2 RUC's rolling 24h window.
WEATHER_HISTORY_START = datetime(
    2026,
    8,
    13,
    0,
    0,
    tzinfo=timezone.utc,
)

# DWD ICON D2 RUC provides 28 lead time forecasts from 0 to 27 hours. I restrict
# accepted lead times (i.e. partition dimensions) to a smaller set because there
# is no business reasons to indiscriminately grab all data. Which lead
# times/data we collect, should be grounded in business logic.
WEATHER_LEAD_TIMES = (
    "PT000H00M",
    "PT001H00M",
    "PT002H00M",
    "PT006H00M",
    "PT012H00M",
    "PT024H00M",
)


RUN_TIME_PARTITIONS = dg.HourlyPartitionsDefinition(
    start_date=WEATHER_HISTORY_START,
    timezone="UTC",
    fmt=RUN_TIME_PARTITION_FORMAT,
    end_offset = 1
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
