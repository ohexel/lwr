from src.dagster_pipeline.assets.database_hostrada_monthly import (
    HOSTRADA_MONTHLY_ASSETS,
)
from src.dagster_pipeline.jobs import HOSTRADA_MONTHLY_JOB
from src.dagster_pipeline.partitions import HOSTRADA_MONTHLY_PARTITIONS


def test_hostrada_monthly_assets_expose_source_and_both_outputs():
    keys = {
        key.to_user_string()
        for asset in HOSTRADA_MONTHLY_ASSETS
        for key in asset.keys
    }

    assert keys == {
        "raw/hostrada_month_files",
        "raw/hostrada_month_source",
        "analytical/hostrada_plr_hourly",
        "analytical/hostrada_berlin_hourly",
    }
    assert all(
        asset.partitions_def is HOSTRADA_MONTHLY_PARTITIONS
        for asset in HOSTRADA_MONTHLY_ASSETS
    )


def test_hostrada_monthly_job_is_separate_from_forecast_and_spatial_jobs():
    assert HOSTRADA_MONTHLY_JOB.name == "hostrada_monthly"
