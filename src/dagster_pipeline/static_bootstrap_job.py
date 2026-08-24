"""Materialize the static inputs required by the operational pipeline."""

import dagster as dg

from src.dagster_pipeline.assets.database_bridge import (
    normalized_icon_plr_area_bridge,
)
from src.dagster_pipeline.assets.database_population import (
    normalized_afs_population_quality_gate,
    raw_afs_population,
)
from src.dagster_pipeline.assets.database_spatial import (
    normalized_icon_cell,
    normalized_plr,
    raw_icon_grid,
    raw_lor_plr,
)
from src.dagster_pipeline.assets.database_weather_mask import (
    normalized_icon_weather_mask,
)


OPERATIONAL_STATIC_JOB = dg.define_asset_job(
    name="operational_static_bootstrap",
    selection=dg.AssetSelection.assets(
        raw_lor_plr,
        normalized_plr,
        raw_afs_population,
        normalized_afs_population_quality_gate,
        raw_icon_grid,
        normalized_icon_cell,
        normalized_icon_plr_area_bridge,
        normalized_icon_weather_mask,
    ),
    description=(
        "Materialize the operational LOR geography, AfS population, ICON grid, "
        "area bridge, and Berlin weather mask without HOSTRADA reconstruction."
    ),
)
