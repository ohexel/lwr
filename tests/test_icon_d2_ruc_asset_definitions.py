from src.dagster_pipeline.assets.icon_d2_ruc import (
    RAW_ICON_D2_RUC_ASSETS,
)


def test_icon_d2_ruc_assets_are_separate_and_visible():
    keys = {
        asset.key.to_user_string()
        for asset in RAW_ICON_D2_RUC_ASSETS
    }

    assert keys == {
        "raw_icon_t_2m",
        "raw_icon_relhum_2m",
        "raw_icon_u_10m",
        "raw_icon_v_10m",
    }
