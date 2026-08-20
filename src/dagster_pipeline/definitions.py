import dagster as dg
from src.dagster_pipeline.assets.icon_d2_ruc import (
        ALL_ICON_D2_RUC_ASSETS,
        RAW_ICON_D2_RUC_ASSETS
        )
from src.dagster_pipeline.sensors.dwd_weather import dwd_icon_d2_ruc_availability_sensor
from src.dagster_pipeline.assets.icon_plr_area_bridge import icon_plr_area_bridge
from src.dagster_pipeline.jobs import (
        ICON_D2_RUC_RAW_ACQUISITION_JOB,
        ICON_D2_RUC_WEATHER_JOB,
        FINAL_ANALYTICAL_CHECK_JOB
        )
from src.dagster_pipeline.partitions import WEATHER_PARTITIONS
from src.dagster_pipeline.assets.plr_population import plr_population_quality_gate
from src.dagster_pipeline.assets.plr_weather import plr_weather
# This imports the data function as an asset from src/dagster_pipeline/assets
from src.dagster_pipeline.assets.plr_weather_population import plr_weather_population
# This imports the check functions as an asset from src/dagster_pipeline/checks
from src.dagster_pipeline.checks.plr_weather_population import FINAL_ANALYTICAL_ASSET_CHECKS


defs = dg.Definitions(
    assets=[
        *ALL_ICON_D2_RUC_ASSETS,
        *RAW_ICON_D2_RUC_ASSETS,
        icon_plr_area_bridge,
        plr_population_quality_gate,
        plr_weather,
        plr_weather_population,
    ],
    asset_checks=[
        *FINAL_ANALYTICAL_ASSET_CHECKS,
    ],
    jobs=[
        ICON_D2_RUC_RAW_ACQUISITION_JOB,
        ICON_D2_RUC_WEATHER_JOB,
        FINAL_ANALYTICAL_CHECK_JOB
        ],
    sensors=[dwd_icon_d2_ruc_availability_sensor],
)

