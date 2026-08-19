from datetime import datetime, timezone

import dagster as dg

from src.dagster_pipeline.partitions import (
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
    partition_keys = (
        WEATHER_PARTITIONS.get_partition_keys(
            current_time=datetime(
                2026,
                8,
                19,
                13,
                0,
                tzinfo=timezone.utc,
            )
        )
    )

    assert any(
        key.keys_by_dimension
        == {
            "run_time": "20260819T1200",
            "lead_time": "PT000H00M",
        }
        for key in partition_keys
    )
