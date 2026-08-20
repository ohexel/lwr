import pandas as pd
import pytest

from src.build_plr_weather_population import (
    join_plr_weather_population,
)


def _weather():
    return pd.DataFrame(
        {
            "plr_id": [
                f"{i:08d}"
                for i in range(542)
            ],
            "temperature_c": 25.0,
        }
    )


def _accepted():
    return pd.DataFrame(
        {
            "plr_id": [
                f"{i:08d}"
                for i in range(540)
            ],
            "population_total": 100,
            "population_65plus": 20,
            "share_65plus": 0.2,
        }
    )


def _rejected():
    return pd.DataFrame(
        {
            "plr_id": [
                "00000540",
                "00000541",
            ],
            "population_total": [
                None,
                None,
            ],
            "population_65plus": [
                None,
                None,
            ],
            "share_65plus": [
                None,
                None,
            ],
            "rejection_reason": [
                "missing_population_total",
                "missing_population_total",
            ],
        }
    )


def test_final_join_explains_every_unmatched_plr():
    result = join_plr_weather_population(
        weather=_weather(),
        accepted_population=_accepted(),
        rejected_population=_rejected(),
    )

    assert len(result) == 542
    assert (
        result["population_status"]
        .value_counts()
        .to_dict()
        == {
            "available": 540,
            "rejected_source_record": 2,
        }
    )


def test_final_join_rejects_unexplained_missing_population():
    rejected = _rejected().iloc[:1]

    with pytest.raises(
        ValueError,
        match="rejection registry",
    ):
        join_plr_weather_population(
            weather=_weather(),
            accepted_population=_accepted(),
            rejected_population=rejected,
        )
