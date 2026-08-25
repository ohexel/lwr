"""Manually materialize one forecast partition without waiting for the sensor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import dagster as dg
import requests

from src.bootstrap import ensure_dagster_home
from src.dagster_pipeline.partitions import (
    WEATHER_LEAD_TIMES,
    weather_partition_key,
)
from src.dwd_icon_d2_ruc import field_url, make_session
from src.dwd_weather_availability import (
    REQUIRED_WEATHER_INDICATORS,
    check_forecast_availability,
)
from src.forecast_key import (
    ForecastKey,
    ProjectPaths,
    RUN_LABEL_FORMAT,
    parse_lead_time,
)
from src.retention.forecast_policy import (
    forecast_partition_window_start,
    forecast_retention_hours,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ForecastUnavailableError(RuntimeError):
    """The requested partition cannot be acquired or materialized."""


def ensure_forecast_sources_available(
    forecast: ForecastKey,
    *,
    project_root: Path = PROJECT_ROOT,
    session_factory: Callable[[], requests.Session] = make_session,
) -> None:
    """Check only source fields that have not already been retained locally."""
    paths = ProjectPaths(project_root=project_root)
    remote_indicators = tuple(
        indicator
        for indicator in REQUIRED_WEATHER_INDICATORS
        if not paths.raw_icon_field(
            indicator=indicator,
            forecast=forecast,
        ).is_file()
    )

    # Retained raw GRIB files remain reprocessable after DWD removes a run.
    if not remote_indicators:
        return

    with session_factory() as session:
        availability = check_forecast_availability(
            session,
            forecast=forecast,
            indicators=remote_indicators,
        )

    if availability.complete:
        return

    first_missing = availability.missing_indicators[0]
    unavailable_fields = ", ".join(availability.missing_indicators)
    raise ForecastUnavailableError(
        f"Forecast run {forecast.run_time:%Y-%m-%d %H:%M} UTC "
        f"with lead time {forecast.lead_time_label} is unavailable from DWD.\n"
        "Recent runs may not yet be published; older runs may no longer "
        "be retained by DWD.\n"
        f"Unavailable fields: {unavailable_fields}.\n"
        f"Example source: {field_url(first_missing, forecast)}"
    )


def run_forecast(
    run_time_label: str,
    lead_time_label: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, str]:
    if lead_time_label not in WEATHER_LEAD_TIMES:
        raise ValueError(
            "Unsupported project lead time: "
            f"{lead_time_label}; choose one of {', '.join(WEATHER_LEAD_TIMES)}"
        )

    try:
        run_time = datetime.strptime(run_time_label, RUN_LABEL_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            "Run time must use YYYYMMDDTHHMM in UTC, "
            "for example 20260824T1600."
        ) from exc

    if run_time.minute != 0:
        raise ValueError("Forecast run time must begin on a full UTC hour.")
    oldest_supported_run = forecast_partition_window_start()
    if run_time < oldest_supported_run:
        raise ForecastUnavailableError(
            f"Forecast run {run_time:%Y-%m-%d %H:%M} UTC is outside the "
            f"configured {forecast_retention_hours()}-hour retention window. "
            f"Choose a run at or after "
            f"{oldest_supported_run:%Y-%m-%d %H:%M} UTC."
        )
    if run_time > datetime.now(timezone.utc):
        raise ForecastUnavailableError(
            f"Forecast run {run_time:%Y-%m-%d %H:%M} UTC is in the future. "
            "Run labels use UTC, not Berlin-local time."
        )

    forecast = ForecastKey(
        run_time=run_time,
        lead_time=parse_lead_time(lead_time_label),
    )
    ensure_forecast_sources_available(forecast, project_root=project_root)
    partition_key = weather_partition_key(forecast)
    ensure_dagster_home(project_root)

    from src.dagster_pipeline.definitions import defs

    instance = dg.DagsterInstance.get()
    try:
        result = defs.get_job_def(
            "icon_d2_ruc_forecast"
        ).execute_in_process(
            partition_key=partition_key,
            instance=instance,
            raise_on_error=False,
        )
    finally:
        instance.dispose()

    if not result.success:
        raise RuntimeError(
            "Forecast materialization failed; inspect Dagster run "
            f"{result.run_id}."
        )

    return {
        "run_id": result.run_id,
        "run_time_utc": forecast.run_time.isoformat(),
        "lead_time": forecast.lead_time_label,
        "valid_time_utc": forecast.valid_time.isoformat(),
        "serving_view": "analytical.current_plr_weather_context",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one forecast partition directly without starting the sensor. "
            "Recent runs may not yet be published; older runs may no longer "
            "be available from DWD."
        )
    )
    parser.add_argument(
        "--run-time",
        required=True,
        help=(
            "Model run label in UTC, not Berlin-local time; "
            "for example 20260824T1600."
        ),
    )
    parser.add_argument(
        "--lead-time",
        default="PT000H00M",
        choices=WEATHER_LEAD_TIMES,
    )
    arguments = parser.parse_args(argv)
    try:
        result = run_forecast(arguments.run_time, arguments.lead_time)
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
