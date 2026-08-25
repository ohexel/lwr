from datetime import datetime, timezone
from types import SimpleNamespace

import dagster as dg
import pytest

from src.dagster_pipeline.definitions import defs
from src.dagster_pipeline.sensors import dwd_weather
from src.dwd_weather_availability import (
    ForecastAvailability,
    WeatherAvailabilityDecision,
)
from src.forecast_key import ForecastKey


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


@pytest.fixture
def sensor_instance():
    with dg.DagsterInstance.ephemeral() as instance:
        yield instance


def _forecast(
    lead_time: str = "PT000H00M",
) -> ForecastKey:
    current_run = datetime.now(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return ForecastKey.from_dwd_labels(
        run_time=current_run.strftime("%Y-%m-%dT%H:%M"),
        lead_time=lead_time,
    )


def _patch_run_discovery(
    monkeypatch,
):
    monkeypatch.setattr(
        dwd_weather,
        "dwd_polling_window_open",
        lambda: True,
    )
    monkeypatch.setattr(
        dwd_weather,
        "make_session",
        lambda: FakeSession(),
    )
    monkeypatch.setattr(
        dwd_weather,
        "advertised_run_times",
        lambda session, indicator: [_forecast().run_time],
    )


def _patch_previous_runs(
    monkeypatch,
    runs,
):
    monkeypatch.setattr(
        dg.DagsterInstance,
        "get_runs",
        lambda self, *args, **kwargs: runs,
    )


def _sensor_context(
    instance: dg.DagsterInstance,
):
    return dg.build_sensor_context(
        instance=instance,
        repository_def=defs.get_repository_def(),
    )


def test_sensor_incomplete_partition_has_no_run_request(
    monkeypatch,
    sensor_instance,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=(),
        latest_incomplete=(
            ForecastAvailability(
                forecast=forecast,
                missing_indicators=(
                    "U_10M",
                ),
            )
        ),
        checked_forecasts=1,
        already_complete_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        lambda *args, **kwargs: decision,
    )

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            _sensor_context(
                sensor_instance
            )
        )
    )

    assert tick.run_requests == []
    assert "U_10M" in tick.skip_message


def test_sensor_emits_all_ready_partitions_as_first_attempts(
    monkeypatch,
    sensor_instance,
):
    _patch_run_discovery(monkeypatch)

    lead_zero = _forecast(
        "PT000H00M"
    )
    lead_one = _forecast(
        "PT001H00M"
    )

    decision = WeatherAvailabilityDecision(
        ready=(
            ForecastAvailability(
                forecast=lead_zero,
                missing_indicators=(),
            ),
            ForecastAvailability(
                forecast=lead_one,
                missing_indicators=(),
            ),
        ),
        latest_incomplete=None,
        checked_forecasts=2,
        already_complete_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        lambda *args, **kwargs: decision,
    )

    _patch_previous_runs(
        monkeypatch,
        [],
    )

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            _sensor_context(
                sensor_instance
            )
        )
    )

    assert len(tick.run_requests) == 2

    requests_by_lead = {
        request.tags[
            "forecast_lead_time"
        ]: request
        for request in tick.run_requests
    }

    assert set(requests_by_lead) == {
        "PT000H00M",
        "PT001H00M",
    }

    zero_request = requests_by_lead[
        "PT000H00M"
    ]

    assert zero_request.partition_key == (
        dg.MultiPartitionKey(
            {
                "run_time": (
                    lead_zero.run_label
                ),
                "lead_time": (
                    "PT000H00M"
                ),
            }
        )
    )

    assert zero_request.run_key == (
        "dwd_icon_d2_ruc_forecast:"
        f"{lead_zero.run_label}:"
        "PT000H00M:"
        "attempt_1"
    )

    assert zero_request.tags["attempt"] == "1"
    assert zero_request.tags["weather_run_scope"] == "forecast_pipeline"

    one_request = requests_by_lead[
        "PT001H00M"
    ]

    assert one_request.run_key == (
        "dwd_icon_d2_ruc_forecast:"
        f"{lead_one.run_label}:"
        "PT001H00M:"
        "attempt_1"
    )
    assert (
        one_request.tags["attempt"]
        == "1"
    )


def test_sensor_limits_forecast_run_requests_per_tick(
    monkeypatch,
    sensor_instance,
):
    _patch_run_discovery(monkeypatch)
    observed_discovery_arguments = {}
    ready = tuple(
        ForecastAvailability(
            forecast=_forecast(f"PT{hour:03d}H00M"),
            missing_indicators=(),
        )
        for hour in range(8)
    )

    def limited_discovery(*args, **kwargs):
        observed_discovery_arguments.update(kwargs)
        return WeatherAvailabilityDecision(
            ready=ready,
            latest_incomplete=None,
            checked_forecasts=len(ready),
            already_complete_forecasts=0,
        )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        limited_discovery,
    )
    _patch_previous_runs(monkeypatch, [])

    tick = dwd_weather.dwd_icon_d2_ruc_availability_sensor.evaluate_tick(
        _sensor_context(sensor_instance)
    )

    assert observed_discovery_arguments["max_ready_forecasts"] == 5
    assert len(tick.run_requests) == 5
    assert [request.tags["forecast_lead_time"] for request in tick.run_requests] == [
        f"PT{hour:03d}H00M" for hour in range(5)
    ]


def test_sensor_retries_failed_partition_with_new_attempt(
    monkeypatch,
    sensor_instance,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=(
            ForecastAvailability(
                forecast=forecast,
                missing_indicators=(),
            ),
        ),
        latest_incomplete=None,
        checked_forecasts=1,
        already_complete_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        lambda *args, **kwargs: decision,
    )

    _patch_previous_runs(
        monkeypatch,
        [
            SimpleNamespace(
                status=(
                    dg.DagsterRunStatus.FAILURE
                )
            )
        ],
    )

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            _sensor_context(
                sensor_instance
            )
        )
    )

    assert len(tick.run_requests) == 1

    request = tick.run_requests[0]

    assert request.run_key == (
        "dwd_icon_d2_ruc_forecast:"
        f"{forecast.run_label}:"
        "PT000H00M:"
        "attempt_2"
    )
    assert request.tags["attempt"] == "2"
    assert request.tags["weather_run_scope"] == "forecast_pipeline"


@pytest.mark.parametrize(
    "status",
    [
        dg.DagsterRunStatus.STARTED,
        dg.DagsterRunStatus.SUCCESS,
    ],
)
def test_sensor_does_not_duplicate_active_or_successful_partition(
    monkeypatch,
    sensor_instance,
    status,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=(
            ForecastAvailability(
                forecast=forecast,
                missing_indicators=(),
            ),
        ),
        latest_incomplete=None,
        checked_forecasts=1,
        already_complete_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        lambda *args, **kwargs: decision,
    )

    _patch_previous_runs(
        monkeypatch,
        [
            SimpleNamespace(
                status=status
            )
        ],
    )

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            _sensor_context(
                sensor_instance
            )
        )
    )

    assert tick.run_requests == []


def test_sensor_stops_after_maximum_attempts(
    monkeypatch,
    sensor_instance,
):
    _patch_run_discovery(monkeypatch)
    forecast = _forecast()

    decision = WeatherAvailabilityDecision(
        ready=(
            ForecastAvailability(
                forecast=forecast,
                missing_indicators=(),
            ),
        ),
        latest_incomplete=None,
        checked_forecasts=1,
        already_complete_forecasts=0,
    )

    monkeypatch.setattr(
        dwd_weather,
        "find_ready_weather_forecasts",
        lambda *args, **kwargs: decision,
    )

    previous_runs = [
        SimpleNamespace(
            status=(
                dg.DagsterRunStatus.FAILURE
            )
        )
        for _ in range(
            dwd_weather
            .SENSOR_MAX_ATTEMPTS
        )
    ]

    _patch_previous_runs(
        monkeypatch,
        previous_runs,
    )

    tick = (
        dwd_weather
        .dwd_icon_d2_ruc_availability_sensor
        .evaluate_tick(
            _sensor_context(
                sensor_instance
            )
        )
    )

    assert tick.run_requests == []
