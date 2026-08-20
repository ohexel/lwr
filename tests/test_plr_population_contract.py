import numpy as np
import pandas as pd
import pytest

from src.validate_plr_population import (
    KNOWN_MISSING_POPULATION_PLRS,
    validate_plr_population,
)


def _population() -> pd.DataFrame:
    plr_ids = [
        f"{index:08d}"
        for index in range(540)
    ] + sorted(
        KNOWN_MISSING_POPULATION_PLRS
    )

    total = np.full(
        542,
        100,
        dtype="int64",
    )

    age65 = np.full(
        542,
        20.0,
        dtype="float64",
    )

    missing_indices = [
        plr_ids.index(plr_id)
        for plr_id
        in KNOWN_MISSING_POPULATION_PLRS
    ]

    for index in missing_indices:
        age65[index] = np.nan

    share = age65 / total

    return pd.DataFrame(
        {
            "plr_id": plr_ids,
            "population_total": total,
            "population_65plus": age65,
            "share_65plus": share,
        }
    )


def test_population_contract_preserves_known_missing_values():
    metadata = validate_plr_population(
        _population()
    )

    assert metadata["plr_count"] == 542
    assert set(
        metadata[
            "missing_population_65plus_plrs"
        ]
    ) == KNOWN_MISSING_POPULATION_PLRS


def test_population_contract_rejects_zero_filling_known_missing():
    frame = _population()

    frame["population_65plus"] = (
        frame["population_65plus"]
        .fillna(0)
    )
    frame["share_65plus"] = (
        frame["share_65plus"]
        .fillna(0)
    )

    with pytest.raises(
        ValueError,
        match="Unexpected population_65plus missingness",
    ):
        validate_plr_population(
            frame
        )
