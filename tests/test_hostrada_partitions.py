from datetime import datetime, timezone

import dagster as dg

from src.dagster_pipeline.partitions import (
    HOSTRADA_MONTHLY_PARTITIONS,
    WEATHER_PARTITIONS,
    hostrada_month_from_partition,
)


def test_hostrada_monthly_partitions_are_separate_from_forecast_partitions():
    assert isinstance(
        HOSTRADA_MONTHLY_PARTITIONS,
        dg.MonthlyPartitionsDefinition,
    )
    assert isinstance(WEATHER_PARTITIONS, dg.MultiPartitionsDefinition)


def test_hostrada_monthly_partitions_include_historical_and_diagnostic_months():
    keys = HOSTRADA_MONTHLY_PARTITIONS.get_partition_keys(
        current_time=datetime(2026, 7, 1, tzinfo=timezone.utc)
    )

    assert keys[0] == "1995-01"
    assert "2025-12" in keys
    assert "2026-06" in keys
    assert "2026-07" not in keys


def test_hostrada_monthly_partition_round_trip():
    month = hostrada_month_from_partition("2024-02")

    assert month.partition_key == "2024-02"
    assert month.hour_count == 696
