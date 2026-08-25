"""Extract optional historical-year lines for the current forecast horizon."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging

import psycopg

from src.database.connection import database_connection
from src.forecast_key import RUN_LABEL_FORMAT


LOGGER = logging.getLogger(__name__)
HISTORICAL_TABLE = "analytical.plr_temperature_history_25h"
HISTORICAL_VIEW = "analytical.current_plr_temperature_history_25h"
REFRESH_FUNCTION = (
    "analytical.refresh_plr_temperature_history_25h(timestamp with time zone)"
)
HISTORICAL_START_YEAR = 1995
HISTORICAL_END_YEAR = 2025
FORECAST_LEAD_COUNT = 25


def parse_run_time_label(value: str) -> datetime:
    """Accept the same full-hour UTC run label as the forecast runner."""
    try:
        parsed = datetime.strptime(value, RUN_LABEL_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            "Run time must use YYYYMMDDTHHMM in UTC, "
            "for example 20260824T1600."
        ) from exc

    if parsed.minute != 0:
        raise ValueError("Forecast run time must begin on a full UTC hour.")
    return parsed


def refresh_historical_trajectories(
    requested_run_time_utc: datetime | None = None,
) -> dict[str, object]:
    """Refresh one horizon transactionally without contacting HOSTRADA or DWD."""
    if requested_run_time_utc is not None:
        if requested_run_time_utc.tzinfo is None:
            raise ValueError("The requested forecast run time must include UTC.")
        requested_run_time_utc = requested_run_time_utc.astimezone(timezone.utc)

    with database_connection(
        application_name="capstone_historical_temperature_trajectories"
    ) as connection:
        installed = connection.execute(
            """
            SELECT
                to_regclass(%s::TEXT) IS NOT NULL
                AND to_regclass(%s::TEXT) IS NOT NULL
                AND to_regprocedure(%s::TEXT) IS NOT NULL
            """,
            (HISTORICAL_TABLE, HISTORICAL_VIEW, REFRESH_FUNCTION),
        ).fetchone()

        if installed != (True,):
            raise RuntimeError(
                "The historical trajectory extension is not installed. "
                "Run: bash scripts/bootstrap_database.sh"
            )

        current = connection.execute(
            """
            SELECT DISTINCT
                run_time_berlin AT TIME ZONE 'Europe/Berlin'
            FROM analytical.current_plr_temperature_forecast_25h
            """
        ).fetchone()

        if current is None:
            raise RuntimeError(
                "No complete 25-point forecast is available. First run: "
                "python -m src.run_forecast_horizon --run-time YYYYMMDDTHHMM"
            )

        current_run_time_utc = current[0].astimezone(timezone.utc)
        if (
            requested_run_time_utc is not None
            and requested_run_time_utc != current_run_time_utc
        ):
            raise RuntimeError(
                "Historical trajectories can be extracted only for the "
                "current complete forecast horizon: "
                f"{current_run_time_utc.strftime(RUN_LABEL_FORMAT)} UTC."
            )

        LOGGER.info(
            "Extracting only 25 Berlin-local hours across historical years "
            "%s-%s for forecast run %s UTC",
            HISTORICAL_START_YEAR,
            HISTORICAL_END_YEAR,
            current_run_time_utc.strftime(RUN_LABEL_FORMAT),
        )
        observed = connection.execute(
            """
            SELECT
                plr_count,
                historical_year_count,
                lead_hour_count,
                historical_row_count,
                reused_existing
            FROM analytical.refresh_plr_temperature_history_25h(%s)
            """,
            (current_run_time_utc,),
        ).fetchone()

    if observed is None:
        raise RuntimeError("Historical trajectory extraction returned no result.")

    plr_count, year_count, lead_count, row_count, reused_existing = observed
    expected_year_count = HISTORICAL_END_YEAR - HISTORICAL_START_YEAR + 1
    expected_row_count = int(plr_count) * FORECAST_LEAD_COUNT * expected_year_count

    if (
        int(plr_count) < 1
        or int(year_count) != expected_year_count
        or int(lead_count) != FORECAST_LEAD_COUNT
        or int(row_count) != expected_row_count
    ):
        raise RuntimeError(
            "Historical trajectory validation failed: "
            f"PLRs={plr_count}, years={year_count}, "
            f"lead_hours={lead_count}, rows={row_count}."
        )

    return {
        "status": "ready",
        "operation": "already_current" if reused_existing else "refreshed",
        "run_time_utc": current_run_time_utc.isoformat(),
        "historical_start_year": HISTORICAL_START_YEAR,
        "historical_end_year": HISTORICAL_END_YEAR,
        "plr_count": int(plr_count),
        "historical_year_count": int(year_count),
        "lead_hour_count": int(lead_count),
        "historical_row_count": int(row_count),
        "historical_table": HISTORICAL_TABLE,
        "plotting_view": HISTORICAL_VIEW,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract historical 1995-2025 Berlin-local temperature "
            "trajectories for the current complete 25-point forecast. "
            "Requires the original HOSTRADA hourly PostgreSQL observations; "
            "the compact reference snapshot alone is insufficient."
        )
    )
    parser.add_argument(
        "--run-time",
        help=(
            "Optional model run label in UTC, not Berlin-local time; "
            "defaults to the current complete forecast horizon."
        ),
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )

    try:
        requested = (
            parse_run_time_label(arguments.run_time)
            if arguments.run_time is not None
            else None
        )
        result = refresh_historical_trajectories(requested)
    except (ValueError, RuntimeError, psycopg.Error) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
