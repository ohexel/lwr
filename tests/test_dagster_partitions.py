from datetime import datetime, timezone

import dagster as dg

from src.dagster_pipeline.partitions import (
    FORECAST_HORIZON_POINT_COUNT,
    LEAD_TIME_PARTITIONS,
    RUN_TIME_PARTITIONS,
    WEATHER_LEAD_TIMES,
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
    weather_partition_key,
)
from src.forecast_key import ForecastKey


def test_weather_partition_round_trip():
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT000H00M",
    )

    partition_key = weather_partition_key(
        forecast
    )

    assert isinstance(
        partition_key,
        dg.MultiPartitionKey,
    )
    assert partition_key.keys_by_dimension == {
        "run_time": "20260819T1200",
        "lead_time": "PT000H00M",
    }

    reconstructed = (
        forecast_key_from_partition(
            partition_key
        )
    )

    assert reconstructed == forecast


def test_hourly_partition_definition_contains_expected_run():
    current_time = datetime.now(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    partition_keys = WEATHER_PARTITIONS.get_partition_keys(current_time=current_time)

    assert any(
        key.keys_by_dimension
        == {
            "run_time": current_time.strftime("%Y%m%dT%H%M"),
            "lead_time": "PT000H00M",
        }
        for key in partition_keys
    )


def test_dagster_forecast_window_starts_with_at_most_24_run_partitions():
    current_time = datetime.now(timezone.utc)
    run_keys = RUN_TIME_PARTITIONS.get_partition_keys(current_time=current_time)

    assert 1 <= len(run_keys) <= 24
    assert run_keys[-1] == current_time.strftime("%Y%m%dT%H00")


def test_forecast_horizon_contains_exactly_hourly_leads_zero_through_24():
    assert FORECAST_HORIZON_POINT_COUNT == 25
    assert WEATHER_LEAD_TIMES == tuple(
        f"PT{hour:03d}H00M" for hour in range(25)
    )
    assert tuple(LEAD_TIME_PARTITIONS.get_partition_keys()) == WEATHER_LEAD_TIMES
