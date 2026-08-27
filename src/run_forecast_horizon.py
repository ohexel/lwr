"""Sequentially materialize and verify one complete 25-point forecast horizon."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import requests

from src.database.connection import database_connection
from src.database.weather_state import weather_population_partition_complete
from src.dagster_pipeline.partitions import (
    FORECAST_HORIZON_POINT_COUNT,
    WEATHER_LEAD_TIMES,
)
from src.historical_temperature_trajectories import refresh_historical_trajectories
from src.run_forecast import parse_forecast_request, run_forecast


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BERLIN_TIMEZONE = ZoneInfo("Europe/Berlin")
FORECAST_VIEW = "analytical.current_plr_temperature_forecast_25h"
SUMMARY_VIEW = "analytical.current_plr_temperature_summary_25h"


def ensure_horizon_views_installed() -> None:
    """Fail before source downloads when the additive schema is missing."""
    with database_connection(
        application_name="capstone_forecast_horizon_schema"
    ) as connection:
        installed = connection.execute(
            """
            SELECT
                to_regclass(%s::TEXT) IS NOT NULL
                AND to_regclass(%s::TEXT) IS NOT NULL
            """,
            (FORECAST_VIEW, SUMMARY_VIEW),
        ).fetchone()

    if installed != (True,):
        raise RuntimeError(
            "The 25-point forecast serving views are not installed. "
            "Run: bash scripts/bootstrap_database.sh"
        )


def validate_forecast_horizon(run_time_berlin: datetime) -> dict[str, int]:
    """Require all 25 lead hours and exactly one complete summary per PLR."""
    local_run_time = run_time_berlin.replace(tzinfo=None)
    with database_connection(
        application_name="capstone_forecast_horizon_validation"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM analytical.plr_display_name),
                (
                    SELECT COUNT(*)
                    FROM analytical.current_plr_temperature_forecast_25h
                    WHERE run_time_berlin = %s::TIMESTAMP
                ),
                (
                    SELECT COUNT(DISTINCT lead_hour)
                    FROM analytical.current_plr_temperature_forecast_25h
                    WHERE run_time_berlin = %s::TIMESTAMP
                ),
                (
                    SELECT COUNT(*)
                    FROM analytical.current_plr_temperature_summary_25h
                    WHERE run_time_berlin = %s::TIMESTAMP
                )
            """,
            (local_run_time, local_run_time, local_run_time),
        ).fetchone()

    if observed is None:
        raise RuntimeError("Forecast-horizon validation returned no result.")

    plr_count, forecast_rows, observed_leads, summary_rows = map(int, observed)
    if (
        plr_count < 1
        or forecast_rows != plr_count * FORECAST_HORIZON_POINT_COUNT
        or observed_leads != FORECAST_HORIZON_POINT_COUNT
        or summary_rows != plr_count
    ):
        raise RuntimeError(
            "The requested forecast horizon is not the current complete "
            f"25-point forecast: PLRs={plr_count}, "
            f"forecast_rows={forecast_rows}, distinct_leads={observed_leads}, "
            f"summary_rows={summary_rows}."
        )

    return {
        "plr_count": plr_count,
        "forecast_row_count": forecast_rows,
        "lead_hour_count": observed_leads,
        "summary_row_count": summary_rows,
    }


def run_forecast_horizon(
    run_time_label: str,
    *,
    project_root: Path = PROJECT_ROOT,
    historical_trajectories: bool = False,
) -> dict[str, object]:
    """Run missing lead hours in order and safely resume completed horizons."""
    first_forecast = parse_forecast_request(run_time_label, WEATHER_LEAD_TIMES[0])
    ensure_horizon_views_installed()

    materialized: list[str] = []
    already_complete: list[str] = []

    for lead_index, lead_time in enumerate(WEATHER_LEAD_TIMES, start=1):
        forecast = parse_forecast_request(run_time_label, lead_time)
        if weather_population_partition_complete(forecast):
            already_complete.append(lead_time)
            LOGGER.info(
                "[%02d/%02d] %s already passed its final quality gate; skipping",
                lead_index,
                FORECAST_HORIZON_POINT_COUNT,
                lead_time,
            )
            continue

        LOGGER.info(
            "[%02d/%02d] Materializing forecast lead %s",
            lead_index,
            FORECAST_HORIZON_POINT_COUNT,
            lead_time,
        )
        run_forecast(run_time_label, lead_time, project_root=project_root)
        materialized.append(lead_time)

    local_run_time = first_forecast.run_time.astimezone(BERLIN_TIMEZONE)
    quality = validate_forecast_horizon(local_run_time)

    result: dict[str, object] = {
        "status": "ready",
        "run_time_utc": first_forecast.run_time.isoformat(),
        "run_time_berlin": local_run_time.isoformat(),
        "lead_hours": FORECAST_HORIZON_POINT_COUNT,
        "materialized_count": len(materialized),
        "already_complete_count": len(already_complete),
        "materialized_lead_times": materialized,
        "already_complete_lead_times": already_complete,
        "forecast_view": FORECAST_VIEW,
        "summary_view": SUMMARY_VIEW,
        "quality": quality,
    }

    if historical_trajectories:
        result["historical_trajectories"] = refresh_historical_trajectories(
            first_forecast.run_time
        )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one complete 25-point neighborhood temperature forecast "
            "sequentially, including lead hours 0 through 24. Existing "
            "quality-checked partitions are skipped. Recent runs may not "
            "yet be published; older runs may no longer be available from DWD."
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
        "--historical-trajectories",
        action="store_true",
        help=(
            "Also extract one historical line per year from the original "
            "1995-2025 HOSTRADA hourly observations. The compact reference "
            "snapshot alone is insufficient."
        ),
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )

    try:
        result = run_forecast_horizon(
            arguments.run_time,
            historical_trajectories=arguments.historical_trajectories,
        )
    except (ValueError, RuntimeError, requests.RequestException, psycopg.Error) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
