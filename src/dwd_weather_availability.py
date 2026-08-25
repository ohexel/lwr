from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

import requests

from src.dwd_icon_d2_ruc import field_available
from src.forecast_key import (
    ForecastKey,
    parse_lead_time
)
from src.icon_d2_ruc_indicators import INDICATORS


REQUIRED_WEATHER_INDICATORS = tuple(
    INDICATORS.keys()
)


@dataclass(frozen=True)
class ForecastAvailability:
    forecast: ForecastKey
    missing_indicators: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_indicators


@dataclass(frozen=True)
class WeatherAvailabilityDecision:
    ready: tuple[ForecastAvailability, ...]
    latest_incomplete: ForecastAvailability | None
    checked_forecasts: int
    already_complete_forecasts: int

def dwd_polling_window_open( now: datetime | None = None ) -> bool:
    if now is None:
        now = datetime.now(timezone.utc)
    return 30 <= now.minute <= 59


def check_forecast_availability(
    session: requests.Session,
    *,
    forecast: ForecastKey,
    indicators: Sequence[str] = (
        REQUIRED_WEATHER_INDICATORS
    ),
    field_available_fn: Callable = field_available,
) -> ForecastAvailability:
    """
    Check all required DWD source fields for exactly one ForecastKey.

    The same run time and lead time are passed to every field check.
    """
    missing = tuple(
        indicator
        for indicator in indicators
        if not field_available_fn(
            session,
            indicator=indicator,
            forecast=forecast,
        )
    )

    return ForecastAvailability(
        forecast=forecast,
        missing_indicators=missing,
    )


def find_ready_weather_forecasts(
    session: requests.Session,
    *,
    advertised_run_times: Iterable[datetime],
    lead_time_labels: Sequence[str],
    minimum_run_time: datetime,
    max_run_times: int = 6,
    max_ready_forecasts: int | None = None,
    already_complete_fn: Callable[
        [ForecastKey],
        bool,
    ],
    field_available_fn: Callable = field_available,
) -> WeatherAvailabilityDecision:
    """
    Find complete weather partitions that are not already complete according to the injected completion predicate.

    Only a small recent run window is inspected. This keeps the sensor
    lightweight while prioritizing acquisition of source data that may
    disappear from DWD's rolling upstream window.

    If the newest pending forecast is incomplete, older recent
    candidates are still checked so one incomplete run does not block a
    complete partition behind it.
    """
    if max_ready_forecasts is not None and max_ready_forecasts < 1:
        raise ValueError("max_ready_forecasts must be positive")

    run_times = sorted(
        {
            run_time
            for run_time in advertised_run_times
            if run_time >= minimum_run_time
        },
        reverse=True,
    )[:max_run_times]

    ready: list[ForecastAvailability] = []
    latest_incomplete = None
    checked = 0
    already_complete = 0

    for run_time in run_times:
        for lead_time_label in lead_time_labels:
            forecast = ForecastKey(
                run_time=run_time,
                lead_time=parse_lead_time(
                    lead_time_label
                ),
            )

            if already_complete_fn(forecast):
                already_complete += 1
                continue

            checked += 1

            availability = (
                check_forecast_availability(
                    session,
                    forecast=forecast,
                    field_available_fn=(
                        field_available_fn
                    ),
                )
            )

            if availability.complete:
                ready.append(availability)
                if (
                    max_ready_forecasts is not None
                    and len(ready) >= max_ready_forecasts
                ):
                    return WeatherAvailabilityDecision(
                        ready=tuple(ready),
                        latest_incomplete=latest_incomplete,
                        checked_forecasts=checked,
                        already_complete_forecasts=already_complete,
                    )
                continue

            if latest_incomplete is None:
                latest_incomplete = availability

    return WeatherAvailabilityDecision(
        ready=tuple(ready),
        latest_incomplete=latest_incomplete,
        checked_forecasts=checked,
        already_complete_forecasts=(
            already_complete
        ),
    )
