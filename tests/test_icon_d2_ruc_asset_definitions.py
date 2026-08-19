from src.dagster_pipeline.assets.icon_d2_ruc import (
    ALL_ICON_D2_RUC_ASSETS,
)


def test_icon_d2_ruc_assets_are_separate_and_visible():
    keys = {
        asset.key.to_user_string()
        for asset in ALL_ICON_D2_RUC_ASSETS
    }

    assert keys == {
        "raw_icon_t_2m",
        "normalized_icon_t_2m",
        "raw_icon_relhum_2m",
        "normalized_icon_relhum_2m",
        "raw_icon_td_2m",
        "normalized_icon_td_2m",
        "raw_icon_u_10m",
        "normalized_icon_u_10m",
        "raw_icon_v_10m",
        "normalized_icon_v_10m",
    }
