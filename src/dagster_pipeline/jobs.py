import dagster as dg

from src.dagster_pipeline.assets.database_analytical import (
    analytical_plr_weather,
    analytical_plr_weather_population,
)
from src.dagster_pipeline.assets.database_weather_normalized import (
    normalized_icon_d2_ruc_weather,
)
from src.dagster_pipeline.assets.database_weather_raw import (
    raw_icon_d2_ruc_field,
)
from src.dagster_pipeline.assets.icon_d2_ruc import (
    RAW_ICON_D2_RUC_ASSETS,
)


ICON_D2_RUC_RAW_ACQUISITION_JOB = dg.define_asset_job(
    name="icon_d2_ruc_raw_acquisition",
    selection=dg.AssetSelection.assets(
        *RAW_ICON_D2_RUC_ASSETS,
    ),
    description=(
        "Acquire and validate the five retained raw ICON D2 RUC "
        "GRIB fields for one forecast partition."
    ),
)


ICON_D2_RUC_FORECAST_JOB = dg.define_asset_job(
    name="icon_d2_ruc_forecast",
    selection=dg.AssetSelection.assets(
        *RAW_ICON_D2_RUC_ASSETS,
        raw_icon_d2_ruc_field,
        normalized_icon_d2_ruc_weather,
        analytical_plr_weather,
        analytical_plr_weather_population,
    ),
    description=(
        "Acquire one ICON D2 RUC forecast partition, load the "
        "Berlin-scoped source values, normalize the weather fields, "
        "area-weight them to PLRs, and join population exposure."
    ),
)
