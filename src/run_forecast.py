"""Manually materialize one forecast partition without waiting for the sensor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import dagster as dg

from src.bootstrap import ensure_dagster_home
from src.dagster_pipeline.partitions import WEATHER_LEAD_TIMES, weather_partition_key
from src.forecast_key import ForecastKey, RUN_LABEL_FORMAT, parse_lead_time


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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

    run_time = datetime.strptime(run_time_label, RUN_LABEL_FORMAT).replace(
        tzinfo=timezone.utc
    )
    forecast = ForecastKey(
        run_time=run_time,
        lead_time=parse_lead_time(lead_time_label),
    )
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
            "Forecast materialization failed; inspect the corresponding Dagster run"
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
        description="Run one forecast partition directly without starting the sensor."
    )
    parser.add_argument(
        "--run-time",
        required=True,
        help="UTC model run label, for example 20260824T1200.",
    )
    parser.add_argument(
        "--lead-time",
        default="PT000H00M",
        choices=WEATHER_LEAD_TIMES,
    )
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            run_forecast(arguments.run_time, arguments.lead_time),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
