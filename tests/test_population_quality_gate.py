import numpy as np
import pandas as pd

from src.build_plr_population_65plus import (
    split_population_quality,
)


def test_quality_gate_splits_and_reconstructs_source():
    rows = []

    for index in range(542):
        rows.append(
            {
                "plr_id": f"{index:08d}",
                "population_total": 100.0,
                "population_65_79": 15.0,
                "population_80plus": 5.0,
                "population_65plus": 20.0,
                "share_65plus": 0.2,
            }
        )

    frame = pd.DataFrame(rows)

    frame.loc[
        frame["plr_id"] == "00000540",
        [
            "population_total",
            "population_65_79",
            "population_80plus",
            "population_65plus",
            "share_65plus",
        ],
    ] = np.nan

    frame.loc[
        frame["plr_id"] == "00000541",
        [
            "population_80plus",
            "population_65plus",
            "share_65plus",
        ],
    ] = np.nan

    accepted, rejected, summary = (
        split_population_quality(frame)
    )

    assert len(accepted) == 540
    assert len(rejected) == 2
    assert len(accepted) + len(rejected) == 542
    assert summary["source_row_count"] == 542
    assert (
        summary["rejection_reasons"]
        ["missing_population_total"]
        == 1
    )
    assert (
        summary["rejection_reasons"]
        ["missing_population_65plus_component"]
        == 1
    )
