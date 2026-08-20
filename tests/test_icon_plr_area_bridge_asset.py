import pandas as pd
import pytest

from src.validate_icon_plr_area_bridge import (
    validate_icon_plr_area_bridge,
)


def _bridge() -> pd.DataFrame:
    rows = []

    for index in range(542):
        plr_id = f"{index:08d}"

        rows.extend(
            [
                {
                    "plr_id": plr_id,
                    "cell_index": index * 2,
                    "intersection_area_m2": 25.0,
                    "plr_area_m2": 100.0,
                    "icon_cell_area_m2": 200.0,
                    "fraction_of_plr": 0.25,
                    "fraction_of_icon_cell": 0.125,
                },
                {
                    "plr_id": plr_id,
                    "cell_index": index * 2 + 1,
                    "intersection_area_m2": 75.0,
                    "plr_area_m2": 100.0,
                    "icon_cell_area_m2": 200.0,
                    "fraction_of_plr": 0.75,
                    "fraction_of_icon_cell": 0.375,
                },
            ]
        )

    return pd.DataFrame(rows)


def test_bridge_contract_accepts_complete_plr_coverage():
    metadata = (
        validate_icon_plr_area_bridge(
            _bridge()
        )
    )

    assert metadata["plr_count"] == 542
    assert metadata["row_count"] == 1084
    assert (
        metadata[
            "max_fraction_of_plr_deviation"
        ]
        == pytest.approx(0.0)
    )


def test_bridge_contract_rejects_incomplete_weights():
    bridge = _bridge()

    mask = (
        bridge["plr_id"] == "00000000"
    )
    bridge.loc[
        mask,
        "fraction_of_plr",
    ] = [0.25, 0.50]

    with pytest.raises(
        ValueError,
        match="sum to approximately 1",
    ):
        validate_icon_plr_area_bridge(
            bridge
        )
