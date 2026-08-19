from datetime import datetime, timezone

import dagster as dg

from src.dagster_pipeline.sensors import (
    dwd_weather,
)
from src.dwd_weather_availability import (
    ForecastAvailability,
    WeatherAvailabilityDecision,
)
from src.forecast_key import ForecastKey

from src.dagster_pipeline.definitions import defs

class FakeSession:
    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False


def _forecast() -> ForecastKey:
    return ForecastKey.from_dwd_labels(
        run_time="2026-08-19T15:00",
        lead_time="PT000H00M",
    )


def _patch_run_discovery(
    monkeypatch,
):
    monkeypatch.setattr(
        dwd_weather,
        "make_session",
        lambda: FakeSession(),
    )
    monkeypatch.setattr(
        dwd_weather,
        "advertised_run_times",
        lambda session, indicator: [
            datetime(
                2026,
                8,
                19,
                15,
                tzinfo=timezone.utc,
            )
        ],
    )


def test_sensor_incomplete_partition_has_no_run_request(
    monkeypatch,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=None,
        latest_incomplete=(
            ForecastAvailability(
                forecast=forecast,
                missing_indicators=(
                    "U_10M",
                ),
            )
        ),
        checked_forecasts=1,
        already_normalized_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecast",
        lambda *args, **kwargs: decision,
    )

    repository_def = defs.get_repository_def()

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            dg.build_sensor_context(
                repository_def = repository_def
            )
        )
    )

    assert tick.run_requests == []
    assert "U_10M" in tick.skip_message


def test_sensor_complete_partition_returns_exactly_one_run_request(
    monkeypatch,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=ForecastAvailability(
            forecast=forecast,
            missing_indicators=(),
        ),
        latest_incomplete=None,
        checked_forecasts=1,
        already_normalized_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecast",
        lambda *args, **kwargs: decision,
    )

    sensor = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
    )

    repository_def = defs.get_repository_def()

    first_tick = sensor.evaluate_tick(
        dg.build_sensor_context(
            repository_def = repository_def
        )
    )
    second_tick = sensor.evaluate_tick(
        dg.build_sensor_context(
            repository_def = repository_def
        )
    )

    assert len(first_tick.run_requests) == 1

    request = first_tick.run_requests[0]

    assert request.partition_key == (
        dg.MultiPartitionKey(
            {
                "run_time": (
                    "20260819T1500"
                ),
                "lead_time": (
                    "PT000H00M"
                ),
            }
        )
    )

    assert request.run_key == (
        "dwd_icon_d2_ruc:"
        "20260819T1500:"
        "PT000H00M"
    )

    # The same forecast always receives the same run key.
    # Dagster's sensor daemon uses that stable run key for
    # duplicate-run protection.
    assert (
        second_tick.run_requests[0].run_key
        == request.run_key
    )
