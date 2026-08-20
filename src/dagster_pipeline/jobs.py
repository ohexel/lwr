import dagster as dg
from src.dagster_pipeline.assets.plr_weather_population import plr_weather_population

from src.dagster_pipeline.assets.icon_d2_ruc import (
        ALL_ICON_D2_RUC_ASSETS,
        RAW_ICON_D2_RUC_ASSETS
        )


ICON_D2_RUC_RAW_ACQUISITION_JOB = dg.define_asset_job(
        name = "icon_d2_ruc_raw_acquisition",
        selection = dg.AssetSelection.assets(
            *RAW_ICON_D2_RUC_ASSETS
            )
        )


ICON_D2_RUC_WEATHER_JOB = dg.define_asset_job(
    name="icon_d2_ruc_weather_ingestion",
    selection=ALL_ICON_D2_RUC_ASSETS,
    description=(
        "Materialize the five raw and five normalized "
        "ICON D2 RUC weather assets for one forecast partition."
    )
)


FINAL_ANALYTICAL_CHECK_JOB = dg.define_asset_job(
        name = "final_analytical_checks",
        description = "Run checks on the final analytical output",
        selection = (
            dg.AssetSelection.assets(plr_weather_population)
            | dg.AssetSelection.checks_for_assets(plr_weather_population)
            )
        )
