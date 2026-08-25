from datetime import datetime, timezone

import pytest

from src.dwd_weather_availability import (
    check_forecast_availability,
    find_ready_weather_forecasts,
)
from src.forecast_key import ForecastKey


def _forecast(
    hour: int,
    lead_time: str = "PT000H00M",
) -> ForecastKey:
    return ForecastKey.from_dwd_labels(
        run_time=f"2026-08-19T{hour:02d}:00",
        lead_time=lead_time,
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


def test_finder_returns_all_complete_pending_forecasts():
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

    decision = find_ready_weather_forecasts(
        object(),
        advertised_run_times=run_times,
        lead_time_labels=(
            "PT000H00M",
            "PT001H00M",
        ),
        minimum_run_time=datetime(
            2026,
            8,
            13,
            tzinfo=timezone.utc,
        ),
        already_complete_fn=(
            lambda forecast: False
        ),
        field_available_fn=fake_available,
    )

    assert [
        (
            availability.forecast.run_label,
            availability.forecast.lead_time_label,
        )
        for availability in decision.ready
    ] == [
        (
            "20260819T1500",
            "PT000H00M",
        ),
        (
            "20260819T1500",
            "PT001H00M",
        ),
        (
            "20260819T1400",
            "PT000H00M",
        ),
        (
            "20260819T1400",
            "PT001H00M",
        ),
    ]

    assert decision.latest_incomplete is None
    assert decision.checked_forecasts == 4
    assert (
        decision.already_complete_forecasts
        == 0
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

    decision = find_ready_weather_forecasts(
        object(),
        advertised_run_times=run_times,
        lead_time_labels=("PT000H00M",),
        minimum_run_time=datetime(
            2026,
            8,
            13,
            tzinfo=timezone.utc,
        ),
        already_complete_fn=(
            lambda forecast: False
        ),
        field_available_fn=fake_available,
    )

    assert [
        availability.forecast.run_label
        for availability in decision.ready
    ] == [
        "20260819T1400",
    ]

    assert (
        decision.latest_incomplete
        is not None
    )
    assert (
        decision.latest_incomplete
        .forecast.run_label
        == "20260819T1500"
    )
    assert (
        decision.latest_incomplete
        .missing_indicators
        == ("V_10M",)
    )


def test_already_complete_forecasts_are_skipped():
    run_times = [
        datetime(
            2026,
            8,
            19,
            15,
            tzinfo=timezone.utc,
        )
    ]

    def fake_already_normalized(
        forecast: ForecastKey,
    ) -> bool:
        return (
            forecast.lead_time_label
            == "PT000H00M"
        )

    def fake_available(
        session,
        *,
        indicator,
        forecast,
    ):
        return True

    decision = find_ready_weather_forecasts(
        object(),
        advertised_run_times=run_times,
        lead_time_labels=(
            "PT000H00M",
            "PT001H00M",
        ),
        minimum_run_time=datetime(
            2026,
            8,
            13,
            tzinfo=timezone.utc,
        ),
        already_complete_fn=(
            fake_already_normalized
        ),
        field_available_fn=fake_available,
    )

    assert [
        availability.forecast.lead_time_label
        for availability in decision.ready
    ] == [
        "PT001H00M",
    ]

    assert (
        decision.already_complete_forecasts
        == 1
    )
    assert decision.checked_forecasts == 1


def test_ready_forecast_limit_stops_source_checks_after_bounded_batch():
    checked_fields = []

    def available(session, *, indicator, forecast):
        checked_fields.append((indicator, forecast.lead_time_label))
        return True

    decision = find_ready_weather_forecasts(
        object(),
        advertised_run_times=[datetime(2026, 8, 19, 15, tzinfo=timezone.utc)],
        lead_time_labels=tuple(f"PT{hour:03d}H00M" for hour in range(25)),
        minimum_run_time=datetime(2026, 8, 19, tzinfo=timezone.utc),
        max_ready_forecasts=5,
        already_complete_fn=lambda forecast: False,
        field_available_fn=available,
    )

    assert len(decision.ready) == 5
    assert decision.checked_forecasts == 5
    assert len(checked_fields) == 20
    assert decision.ready[-1].forecast.lead_time_label == "PT004H00M"


def test_ready_forecast_limit_must_be_positive():
    with pytest.raises(ValueError, match="must be positive"):
        find_ready_weather_forecasts(
            object(),
            advertised_run_times=[],
            lead_time_labels=(),
            minimum_run_time=datetime(2026, 8, 19, tzinfo=timezone.utc),
            max_ready_forecasts=0,
            already_complete_fn=lambda forecast: False,
        )
