import dagster as dg
from src.dagster_pipeline.assets.icon_d2_ruc import ALL_ICON_D2_RUC_ASSETS
from src.dagster_pipeline.assets.icon_plr_area_bridge import icon_plr_area_bridge
from src.dagster_pipeline.assets.plr_population import plr_population_quality_gate
from src.dagster_pipeline.assets.plr_weather import plr_weather
from src.dagster_pipeline.assets.plr_weather_population import plr_weather_population
from src.dagster_pipeline.checks.plr_weather_population import FINAL_ANALYTICAL_ASSET_CHECKS
from src.dagster_pipeline.partitions import WEATHER_PARTITIONS
from src.dagster_pipeline.sensors.dwd_weather import (
    ICON_D2_RUC_WEATHER_JOB,
    dwd_icon_d2_ruc_availability_sensor,
)

FINAL_ANALYTICAL_CHECK_JOB = dg.define_asset_job(
        name = "final_analytical_checks",
        description = "Run checks on the final analytical output",
        selection = (
            dg.AssetSelection.assets(plr_weather_population)
            | dg.AssetSelection.checks_for_assets(plr_weather_population)
            )
        )

defs = dg.Definitions(
    assets=[
        *ALL_ICON_D2_RUC_ASSETS,
        icon_plr_area_bridge,
        plr_population_quality_gate,
        plr_weather,
        plr_weather_population,
    ],
    asset_checks=[
        *FINAL_ANALYTICAL_ASSET_CHECKS,
    ],
    jobs=[
        ICON_D2_RUC_WEATHER_JOB,
        FINAL_ANALYTICAL_CHECK_JOB
        ],
    sensors=[dwd_icon_d2_ruc_availability_sensor],
)

