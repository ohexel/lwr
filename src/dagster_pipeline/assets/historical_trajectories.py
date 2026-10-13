"""Refresh the 31-year historical trajectory cache for the current horizon."""

from datetime import datetime

import dagster as dg
from dagster import AssetExecutionContext

from src.dagster_pipeline.assets.database_analytical import (
    ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
)
from src.historical_temperature_trajectories import refresh_historical_trajectories


HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY = dg.AssetKey(
    ["analytical", "historical_temperature_trajectories"]
)


class HistoricalTrajectoriesConfig(dg.Config):
    """Forecast run identity, supplied by historical_trajectories_sensor."""

    run_time_utc: str


@dg.asset(
    key=HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY,
    deps=[ANALYTICAL_PLR_WEATHER_POPULATION_KEY],
    group_name="analytical",
    description=(
        "Extracts one matching historical line per year, 1995-2025, for "
        "the current complete 25-hour forecast horizon into "
        "analytical.plr_temperature_history_25h. Requires the full, "
        "populated analytical.hostrada_plr_hourly table; the compact "
        "reference snapshot alone is insufficient. Materialized once per "
        "complete horizon by historical_trajectories_sensor, never per "
        "lead-hour partition."
    ),
)
def historical_temperature_trajectories(
    context: AssetExecutionContext,
    config: HistoricalTrajectoriesConfig,
) -> None:
    run_time = datetime.fromisoformat(config.run_time_utc)
    result = refresh_historical_trajectories(run_time)
    context.add_output_metadata(dict(result))


HISTORICAL_TRAJECTORIES_ASSETS = [historical_temperature_trajectories]
