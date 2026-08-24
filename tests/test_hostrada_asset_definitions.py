from src.dagster_pipeline.assets.database_hostrada_spatial import (
    HOSTRADA_SPATIAL_ASSETS,
)
from src.dagster_pipeline.jobs import HOSTRADA_SPATIAL_JOB


def test_hostrada_spatial_assets_are_separate_and_visible():
    keys = {
        asset.key.to_user_string()
        for asset in HOSTRADA_SPATIAL_ASSETS
    }

    assert keys == {
        "normalized/hostrada_cell",
        "normalized/hostrada_plr_area_bridge",
    }


def test_hostrada_spatial_job_is_independent_of_forecast_job():
    assert HOSTRADA_SPATIAL_JOB.name == "hostrada_spatial"
