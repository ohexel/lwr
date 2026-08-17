from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_SILVER_OUTPUT = Path(
    "data/silver/population/plr_population_65plus.parquet"
)
DEFAULT_PLR_TEMPERATURE_ROOT = Path(
    "data/gold/plr-temperature"
)
DEFAULT_GOLD_OUTPUT = Path(
    "data/gold/plr-temperature-population/plr_temperature_population_65plus.parquet"
)

POPULATION_FILENAME = "EWR_L21_202512E_Matrix.csv"


def find_population_file(raw_root: Path) -> Path:
    matches = sorted(
        raw_root.rglob(POPULATION_FILENAME)
    )

    if not matches:
        # Fallback in case the file was renamed but retains the dataset prefix.
        matches = sorted(
            raw_root.rglob("EWR_L21_*_Matrix.csv")
        )

    if not matches:
        raise FileNotFoundError(
            f"Could not find {POPULATION_FILENAME} below {raw_root}"
        )

    if len(matches) > 1:
        print(
            "Multiple population files found; using the first:\n"
            + "\n".join(f"  - {p}" for p in matches)
        )

    return matches[0]


def find_latest_plr_temperature_file(root: Path) -> Path:
    files = sorted(
        root.glob("*/plr_temperature.parquet")
    )

    if not files:
        raise FileNotFoundError(
            f"No plr_temperature.parquet files found below {root}"
        )

    return files[-1]


def build_population_table(
    population_file: Path,
) -> pd.DataFrame:
    print(f"Reading AfS population CSV: {population_file}")

    population = pd.read_csv(
        population_file,
        sep=";",
        dtype=str,
    )

    required = {
        "RAUMID",
        "E_E",
        "E_E65U80",
        "E_E80U110",
    }

    missing = required - set(population.columns)

    if missing:
        raise ValueError(
            "Population file is missing required columns: "
            f"{sorted(missing)}"
        )

    if len(population) != 542:
        raise ValueError(
            f"Expected 542 PLR rows, found {len(population)}"
        )

    numeric_columns = [
        "E_E",
        "E_E65U80",
        "E_E80U110",
    ]

    for column in numeric_columns:
        population[column] = pd.to_numeric(
            population[column],
            errors="raise",
        )

    missing_count = population[column].isna().sum()
    if missing_count:
        print(f"WARNING: {column} contains "
              f"{missing_count} missing values"
              )
        print(population.loc[
            population[column].isna(),
            ["RAUMID", column],
            ].head(20)
              )

    result = pd.DataFrame(
        {
            "plr_id": population["RAUMID"].astype(str),
            "population_total": population["E_E"].astype("Int64"),
            "population_65_79": population["E_E65U80"].astype("Int64"),
            "population_80plus": population["E_E80U110"].astype("Int64"),
        }
    )

    result["population_65plus"] = (
        result["population_65_79"]
        + result["population_80plus"]
    )

    result["share_65plus"] = (
        result["population_65plus"]
        / result["population_total"]
    )

    if result["plr_id"].duplicated().any():
        duplicates = (
            result.loc[
                result["plr_id"].duplicated(),
                "plr_id",
            ]
            .tolist()
        )

        raise ValueError(
            f"Duplicate PLR IDs found: {duplicates[:10]}"
        )

    if (
        result["population_65plus"]
        > result["population_total"]
    ).any():
        raise ValueError(
            "population_65plus exceeds population_total "
            "for at least one PLR"
        )

    return result.sort_values("plr_id").reset_index(drop=True)


def join_with_temperature(
    population: pd.DataFrame,
    plr_temperature_file: Path,
) -> pd.DataFrame:
    print(f"Reading PLR temperature: {plr_temperature_file}")

    temperature = pd.read_parquet(
        plr_temperature_file
    )

    required = {
        "plr_id",
        "run_time_utc",
        "valid_time_utc",
        "temperature_c",
        "temperature_k",
        "icon_cells_used",
        "weight_sum",
    }

    missing = required - set(temperature.columns)

    if missing:
        raise ValueError(
            "Temperature file is missing required columns: "
            f"{sorted(missing)}"
        )

    temperature["plr_id"] = (
        temperature["plr_id"].astype(str)
    )

    joined = temperature.merge(
        population,
        on="plr_id",
        how="left",
        validate="one_to_one",
        indicator = True
    )

    unmatched = joined[
        joined["_merge"] != "both"
    ]

    if not unmatched.empty:
        raise RuntimeError(
            "Some temperature PLRs have no matching population row: "
            f"{len(missing_population)}"
        )

    joined = joined.drop(columns = "_merge")

    return joined.sort_values("plr_id").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build Berlin PLR population age 65+ and join it "
            "to the latest area-weighted PLR temperature table."
        )
    )
    parser.add_argument(
        "--population-file",
        type=Path,
        help=(
            "AfS population CSV. If omitted, search below data/raw."
        ),
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=DEFAULT_RAW_ROOT,
    )
    parser.add_argument(
        "--silver-output",
        type=Path,
        default=DEFAULT_SILVER_OUTPUT,
    )
    parser.add_argument(
        "--plr-temperature-file",
        type=Path,
        help=(
            "Specific plr_temperature.parquet file. "
            "Defaults to latest below data/gold/plr-temperature."
        ),
    )
    parser.add_argument(
        "--plr-temperature-root",
        type=Path,
        default=DEFAULT_PLR_TEMPERATURE_ROOT,
    )
    parser.add_argument(
        "--gold-output",
        type=Path,
        default=DEFAULT_GOLD_OUTPUT,
    )

    args = parser.parse_args()

    population_file = (
        args.population_file
        or find_population_file(args.raw_root)
    )

    plr_temperature_file = (
        args.plr_temperature_file
        or find_latest_plr_temperature_file(
            args.plr_temperature_root
        )
    )

    population = build_population_table(
        population_file
    )

    args.silver_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    population.to_parquet(
        args.silver_output,
        index=False,
    )

    print()
    print("PLR population 65+ complete")
    print("---------------------------")
    print(f"PLRs:                    {len(population):,}")
    print(
        f"Total population:        "
        f"{population['population_total'].sum():,}"
    )
    print(
        f"Population age 65+:      "
        f"{population['population_65plus'].sum():,}"
    )
    print(
        f"Overall share age 65+:   "
        f"{population['population_65plus'].sum() / population['population_total'].sum():.2%}"
    )
    print(f"Silver output:           {args.silver_output}")

    joined = join_with_temperature(
        population,
        plr_temperature_file,
    )

    args.gold_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    joined.to_parquet(
        args.gold_output,
        index=False,
    )

    print()
    print("Temperature + population join complete")
    print("--------------------------------------")
    print(f"Rows:                    {len(joined):,}")
    print(
        f"Min temperature:         "
        f"{joined['temperature_c'].min():.2f} °C"
    )
    print(
        f"Max temperature:         "
        f"{joined['temperature_c'].max():.2f} °C"
    )
    print(f"Gold output:             {args.gold_output}")


if __name__ == "__main__":
    main()
