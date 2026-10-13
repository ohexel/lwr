"""Refresh historical trajectories once a forecast horizon completes.

Note: AssetSensorDefinition unconditionally resets context.cursor to the
triggering event's storage_id after every tick, regardless of what this
function does. A manual cursor-based dedup on run_time is therefore not
viable here; RunRequest.run_key uniqueness (below) is what actually
prevents duplicate runs across ticks.
"""

from zoneinfo import ZoneInfo

import dagster as dg

from src.dagster_pipeline.assets.database_analytical import (
    ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
)
from src.dagster_pipeline.assets.historical_trajectories import (
    historical_temperature_trajectories,
)
from src.dagster_pipeline.jobs import HISTORICAL_TRAJECTORIES_JOB
from src.dagster_pipeline.partitions import forecast_key_from_partition
from src.run_forecast_horizon import validate_forecast_horizon


BERLIN_TIMEZONE = ZoneInfo("Europe/Berlin")


@dg.asset_sensor(
    asset_key=ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
    job=HISTORICAL_TRAJECTORIES_JOB,
    default_status=dg.DefaultSensorStatus.RUNNING,
    minimum_interval_seconds=60,
    description=(
        "Fires when a forecast partition materializes and the 25-lead "
        "horizon it belongs to is now complete, using the same "
        "completeness check as run_forecast_horizon. Driven by Dagster's "
        "materialization event stream, not by cron."
    ),
)
def historical_trajectories_sensor(
    context: dg.SensorEvaluationContext,
    asset_event: dg.EventLogEntry,
):
    partition_key = asset_event.dagster_event.partition
    if partition_key is None:
        return dg.SkipReason(
            "analytical_plr_weather_population materialization has no "
            "forecast partition key."
        )

    forecast = forecast_key_from_partition(partition_key)
    run_time_label = forecast.run_time.isoformat()

    try:
        validate_forecast_horizon(forecast.run_time.astimezone(BERLIN_TIMEZONE))
    except RuntimeError as exc:
        return dg.SkipReason(
            f"Forecast horizon for run {run_time_label} is not yet complete: {exc}"
        )

    return dg.RunRequest(
        # run_key gives idempotency across ticks: Dagster will not launch a
        # second run for a run_time that already has one under this key,
        # even though this sensor has no working cursor-based dedup of
        # its own (see module docstring).
        run_key=f"historical-trajectories-{run_time_label}",
        run_config={
            "ops": {
                historical_temperature_trajectories.op.name: {
                    "config": {"run_time_utc": run_time_label}
                }
            }
        },
    )
