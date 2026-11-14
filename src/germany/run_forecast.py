"""Run one complete 25-point Germany forecast (lead hours 0-24).

Usage:
    uv run --env-file .env python -m src.germany.run_forecast \\
        --run-time "$(date -u -d '3 hours ago' +%Y%m%dT%H00)"

Each lead time is one Dagster partition of the `germany_forecast` job
(the four shared raw DWD fields plus the Germany aggregation). Partitions
already complete for every Gemeinde are skipped. Afterwards the
`germany_forecast_publish` job publishes the run, applies retention and
runs checks 5-7. The Berlin pipeline and its sensor are not involved.
"""

import argparse
import json
import logging
from pathlib import Path
from time import perf_counter

import dagster as dg

from src.bootstrap import ensure_dagster_home
from src.database.connection import database_connection
from src.dagster_pipeline.partitions import WEATHER_LEAD_TIMES, weather_partition_key
from src.germany.contract import GEOGRAPHY_VERSION
from src.run_forecast import ensure_forecast_sources_available, parse_forecast_request


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def partition_complete(forecast) -> bool:
    with database_connection(application_name="lwr_germany_run_state") as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM normalized.de_gemeinde_forecast
                 WHERE run_time_utc = %s AND lead_time = %s
                   AND geography_version = %s
                   AND temperature_c IS NOT NULL
                   AND apparent_temperature_shade_c IS NOT NULL),
                (SELECT COUNT(*) FROM normalized.de_gemeinde
                 WHERE geography_version = %s)
            """,
            (forecast.run_time, forecast.lead_time_label, GEOGRAPHY_VERSION, GEOGRAPHY_VERSION),
        ).fetchone()
    return row is not None and row[1] > 0 and row[0] == row[1]


def execute_job(job_name: str, *, partition_key=None, run_config=None) -> dg.ExecuteInProcessResult:
    from src.dagster_pipeline.definitions import defs

    instance = dg.DagsterInstance.get()
    try:
        return defs.resolve_job_def(job_name).execute_in_process(
            partition_key=partition_key,
            run_config=run_config,
            instance=instance,
            raise_on_error=False,
        )
    finally:
        instance.dispose()


def run_germany_forecast(
    run_time_label: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    ensure_dagster_home(project_root)
    materialized: list[str] = []
    skipped: list[str] = []
    started = perf_counter()

    for index, lead_time in enumerate(WEATHER_LEAD_TIMES, start=1):
        forecast = parse_forecast_request(run_time_label, lead_time)
        if partition_complete(forecast):
            skipped.append(lead_time)
            LOGGER.info("[%02d/25] %s already complete; skipping", index, lead_time)
            continue
        ensure_forecast_sources_available(forecast, project_root=project_root)
        LOGGER.info("[%02d/25] Materializing Germany lead %s", index, lead_time)
        result = execute_job(
            "germany_forecast",
            partition_key=weather_partition_key(forecast),
        )
        if not result.success:
            raise RuntimeError(
                f"Germany partition {lead_time} failed; inspect Dagster run {result.run_id}"
            )
        materialized.append(lead_time)

    partitions_seconds = perf_counter() - started
    publish_started = perf_counter()
    publish = execute_job("germany_forecast_publish")
    if not publish.success:
        raise RuntimeError(f"Publishing failed; inspect Dagster run {publish.run_id}")
    checks = {
        evaluation.check_name: {
            "passed": evaluation.passed,
            "severity": evaluation.severity.value,
        }
        for evaluation in publish.get_asset_check_evaluations()
    }

    return {
        "status": "ready",
        "run_time_utc": parse_forecast_request(run_time_label, WEATHER_LEAD_TIMES[0])
        .run_time.isoformat(),
        "materialized_lead_times": len(materialized),
        "already_complete_lead_times": len(skipped),
        "partitions_wall_seconds": round(partitions_seconds, 1),
        "publish_wall_seconds": round(perf_counter() - publish_started, 1),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-time", required=True, help="UTC run label, e.g. 20261109T1600")
    arguments = parser.parse_args(argv)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
    result = run_germany_forecast(arguments.run_time)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
