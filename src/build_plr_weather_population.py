from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.build_plr_population_65plus import (
    ACCEPTED_POPULATION_PATH,
    REJECTED_POPULATION_PATH,
)
from src.forecast_key import (
    ForecastKey,
    ProjectPaths,
)
from src.validate_plr_population import (
    read_and_validate_population_quality_artifacts,
)


EXPECTED_WEATHER_PLR_COUNT = 542


def join_plr_weather_population(
    *,
    weather: pd.DataFrame,
    accepted_population: pd.DataFrame,
    rejected_population: pd.DataFrame,
) -> pd.DataFrame:
    if len(weather) != EXPECTED_WEATHER_PLR_COUNT:
        raise ValueError(
            "PLR weather must contain "
            f"{EXPECTED_WEATHER_PLR_COUNT} rows; "
            f"got {len(weather)}"
        )

    weather = weather.copy()
    accepted_population = (
        accepted_population.copy()
    )
    rejected_population = (
        rejected_population.copy()
    )

    for frame in (
        weather,
        accepted_population,
        rejected_population,
    ):
        frame["plr_id"] = (
            frame["plr_id"]
            .astype("string")
        )

    if weather["plr_id"].duplicated().any():
        raise ValueError(
            "PLR weather contains duplicate IDs"
        )

    if accepted_population[
        "plr_id"
    ].duplicated().any():
        raise ValueError(
            "Accepted population contains duplicate IDs"
        )

    if rejected_population[
        "plr_id"
    ].duplicated().any():
        raise ValueError(
            "Rejected population contains duplicate IDs"
        )

    population_columns = [
        "plr_id",
        "population_total",
        "population_65plus",
        "share_65plus",
    ]

    merged = weather.merge(
        accepted_population[
            population_columns
        ],
        on="plr_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    unmatched_ids = set(
        merged.loc[
            merged["_merge"] == "left_only",
            "plr_id",
        ].astype(str)
    )

    rejected_ids = set(
        rejected_population[
            "plr_id"
        ].astype(str)
    )

    if unmatched_ids != rejected_ids:
        raise ValueError(
            "Population join exceptions do not "
            "match the rejection registry: "
            f"unmatched={sorted(unmatched_ids)}, "
            f"rejected={sorted(rejected_ids)}"
        )

    rejection_lookup = (
        rejected_population[
            ["plr_id", "rejection_reason"]
        ]
        .set_index("plr_id")[
            "rejection_reason"
        ]
        .to_dict()
    )

    merged["population_status"] = (
        merged["_merge"]
        .astype("string")
        .map(
            {
                "both": "available",
                "left_only": "rejected_source_record",
                "right_only": "unexpected",
            }
        )
        .astype("string")
    )

    merged[
        "population_rejection_reason"
    ] = merged["plr_id"].map(
        rejection_lookup
    )

    merged = merged.drop(
        columns="_merge"
    )

    if len(merged) != EXPECTED_WEATHER_PLR_COUNT:
        raise ValueError(
            "Final analytical dataset lost PLRs"
        )

    return merged


def build_plr_weather_population(
    *,
    forecast: ForecastKey,
    paths: ProjectPaths | None = None,
    accepted_path: Path = ACCEPTED_POPULATION_PATH,
    rejected_path: Path = REJECTED_POPULATION_PATH,
) -> pd.DataFrame:
    project_paths = paths or ProjectPaths()

    weather_path = (
        project_paths.analytical_plr_weather(
            forecast=forecast
        )
    )

    weather = pd.read_parquet(
        weather_path
    )

    accepted, rejected, _ = (
        read_and_validate_population_quality_artifacts(
            accepted_path=accepted_path,
            rejected_path=rejected_path,
        )
    )

    return join_plr_weather_population(
        weather=weather,
        accepted_population=accepted,
        rejected_population=rejected,
    )


def write_plr_weather_population(
    *,
    frame: pd.DataFrame,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    part_path = path.with_name(
        path.name + ".part"
    )

    if part_path.exists():
        part_path.unlink()

    try:
        frame.to_parquet(
            part_path,
            index=False,
        )
        part_path.replace(path)
    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise
