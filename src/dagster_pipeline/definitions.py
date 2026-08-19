import dagster as dg

from src.dagster_pipeline.assets.icon_d2_ruc import (
    ALL_ICON_D2_RUC_ASSETS,
)
from src.dagster_pipeline.sensors.dwd_weather import (
    ICON_D2_RUC_WEATHER_JOB,
    dwd_icon_d2_ruc_availability_sensor,
)


defs = dg.Definitions(
    assets=ALL_ICON_D2_RUC_ASSETS,
    jobs=[
        ICON_D2_RUC_WEATHER_JOB,
    ],
    sensors=[
        dwd_icon_d2_ruc_availability_sensor,
    ],
)
