"""Germany-layer jobs. The Berlin jobs and sensor are unchanged (F)."""

import dagster as dg

from src.dagster_pipeline.assets.germany_forecast import (
    analytical_de_current_forecast_25h,
    normalized_de_gemeinde_forecast,
    normalized_de_icon_gemeinde_bridge,
)
from src.dagster_pipeline.assets.germany_normalized import GERMANY_NORMALIZED_ASSETS
from src.dagster_pipeline.assets.germany_raw import GERMANY_RAW_ASSETS
from src.dagster_pipeline.assets.icon_d2_ruc import RAW_ICON_D2_RUC_ASSETS


GERMANY_STATIC_JOB = dg.define_asset_job(
    name="germany_static",
    selection=dg.AssetSelection.assets(
        *GERMANY_RAW_ASSETS,
        *GERMANY_NORMALIZED_ASSETS,
        normalized_de_icon_gemeinde_bridge,
    ),
    description=(
        "Acquire and load VG250 and Zensus 2022, build the normalized "
        "hierarchy, population and the Germany cell-Gemeinde bridge."
    ),
)

GERMANY_FORECAST_JOB = dg.define_asset_job(
    name="germany_forecast",
    selection=dg.AssetSelection.assets(
        *RAW_ICON_D2_RUC_ASSETS,
        normalized_de_gemeinde_forecast,
    ),
    description=(
        "One forecast partition for Germany: the four retained raw DWD "
        "fields (file-only, shared with Berlin) and the Germany cell and "
        "Gemeinde aggregation."
    ),
)

GERMANY_PUBLISH_JOB = dg.define_asset_job(
    name="germany_forecast_publish",
    selection=dg.AssetSelection.assets(analytical_de_current_forecast_25h),
    description=(
        "Publish the current complete Germany run, apply retention and run "
        "checks 5-7."
    ),
)

GERMANY_JOBS = [GERMANY_STATIC_JOB, GERMANY_FORECAST_JOB, GERMANY_PUBLISH_JOB]
