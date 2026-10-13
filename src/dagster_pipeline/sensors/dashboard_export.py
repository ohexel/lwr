"""Publish the dashboard once a horizon's forecast and history are both ready."""

import dagster as dg

from src.dagster_pipeline.assets.historical_trajectories import (
    HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY,
)
from src.dagster_pipeline.jobs import STATIC_DASHBOARD_EXPORT_JOB


@dg.asset_sensor(
    asset_key=HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY,
    job=STATIC_DASHBOARD_EXPORT_JOB,
    default_status=dg.DefaultSensorStatus.RUNNING,
    minimum_interval_seconds=60,
    description=(
        "Publishes the static dashboard once historical_temperature_"
        "trajectories has materialized for the current complete horizon. "
        "That asset itself only materializes once per complete 25-lead "
        "horizon, so no additional completeness check or cursor is needed "
        "here; Dagster's own event cursor for this asset key prevents "
        "re-triggering on an already-processed materialization."
    ),
)
def dashboard_export_sensor(
    context: dg.SensorEvaluationContext,
    asset_event: dg.EventLogEntry,
) -> dg.RunRequest:
    return dg.RunRequest(run_key=f"dashboard-export-{asset_event.run_id}")
