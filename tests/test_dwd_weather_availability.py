from datetime import datetime, timezone

from src.dwd_weather_availability import (
    check_forecast_availability,
    find_ready_weather_forecast,
)
from src.forecast_key import ForecastKey


def _forecast(hour: int) -> ForecastKey:
    return ForecastKey.from_dwd_labels(
        run_time=(
            f"2026-08-19T{hour:02d}:00"
        ),
        lead_time="PT000H00M",
    )


def test_incomplete_forecast_reports_missing_indicator():
    forecast = _forecast(15)

    def fake_available(
        session,
        *,
        indicator,
        forecast,
    ):
        return indicator != "U_10M"

    result = check_forecast_availability(
        object(),
        forecast=forecast,
        field_available_fn=fake_available,
    )

    assert not result.complete
    assert result.missing_indicators == (
        "U_10M",
    )


def test_finder_returns_newest_complete_pending_partition():
    run_times = [
        datetime(
            2026,
            8,
            19,
            14,
            tzinfo=timezone.utc,
        ),
        datetime(
            2026,
            8,
            19,
            15,
            tzinfo=timezone.utc,
        ),
    ]

    def fake_available(
        session,
        *,
        indicator,
        forecast,
    ):
        return True

    decision = find_ready_weather_forecast(
        object(),
        advertised_run_times=run_times,
        lead_time_labels=("PT000H00M",),
        minimum_run_time=datetime(
            2026,
            8,
            13,
            tzinfo=timezone.utc,
        ),
        already_normalized_fn=(
            lambda forecast: False
        ),
        field_available_fn=fake_available,
    )

    assert decision.ready is not None
    assert (
        decision.ready.forecast.run_label
        == "20260819T1500"
    )


def test_incomplete_newest_run_does_not_block_older_complete_run():
    run_times = [
        datetime(
            2026,
            8,
            19,
            14,
            tzinfo=timezone.utc,
        ),
        datetime(
            2026,
            8,
            19,
            15,
            tzinfo=timezone.utc,
        ),
    ]

    def fake_available(
        session,
        *,
        indicator,
        forecast,
    ):
        if (
            forecast.run_label
            == "20260819T1500"
        ):
            return indicator != "V_10M"
        return True

    decision = find_ready_weather_forecast(
        object(),
        advertised_run_times=run_times,
        lead_time_labels=("PT000H00M",),
        minimum_run_time=datetime(
            2026,
            8,
            13,
            tzinfo=timezone.utc,
        ),
        already_normalized_fn=(
            lambda forecast: False
        ),
        field_available_fn=fake_available,
    )

    assert decision.ready is not None
    assert (
        decision.ready.forecast.run_label
        == "20260819T1400"
    )
    assert (
        decision.latest_incomplete
        is not None
    )
    assert (
        decision.latest_incomplete
        .missing_indicators
        == ("V_10M",)
    )
