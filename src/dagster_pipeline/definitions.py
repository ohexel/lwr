import dagster as dg

from src.dagster_pipeline.assets.icon_d2_ruc import (
    ALL_ICON_D2_RUC_ASSETS,
)


defs = dg.Definitions(
    assets=ALL_ICON_D2_RUC_ASSETS,
)
