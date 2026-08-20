import numpy as np
import pandas as pd
import pytest

from src.build_plr_weather_population import (
    join_plr_weather_population,
)
from src.validate_plr_population import (
    KNOWN_MISSING_POPULATION_PLRS,
)


def _ids():
    return [
        f"{index:08d}"
        for index in range(540)
    ] + sorted(
        KNOWN_MISSING_POPULATION_PLRS
    )


def _weather():
    ids = _ids()

    return pd.DataFrame(
        {
            "plr_id": ids,
            "run_time_utc": pd.Timestamp(
                "2026-08-19T17:00:00Z"
            ),
            "lead_time": "PT000H00M",
            "valid_time_utc": pd.Timestamp(
                "2026-08-19T17:00:00Z"
            ),
            "temperature_c": 25.0,
        }
    )


def _population():
    ids = _ids()

    population_65plus = np.full(
        542,
        20.0,
    )

    for plr_id in (
        KNOWN_MISSING_POPULATION_PLRS
    ):
        population_65plus[
            ids.index(plr_id)
        ] = np.nan

    return pd.DataFrame(
        {
            "plr_id": ids,
            "population_total": 100,
            "population_65plus": (
                population_65plus
            ),
            "share_65plus": (
                population_65plus / 100
            ),
        }
    )


def test_final_join_keeps_all_plrs_and_preserves_known_na():
    result = (
        join_plr_weather_population(
            weather=_weather(),
            population=_population(),
        )
    )

    assert len(result) == 542

    missing_ids = set(
        result.loc[
            result[
                "population_65plus"
            ].isna(),
            "plr_id",
        ].tolist()
    )

    assert (
        missing_ids
        == KNOWN_MISSING_POPULATION_PLRS
    )


def test_final_join_rejects_unmatched_plr():
    population = _population()
    population = population.iloc[:-1]

    with pytest.raises(
        ValueError,
        match="join lost matches",
    ):
        join_plr_weather_population(
            weather=_weather(),
            population=population,
        )
