from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any
import pandas as pd
from src.validate_afs_population import REQUIRED_COLUMNS, normalize_population_columns, add_population_65plus

DEFAULT_INPUT = Path("data/raw/population/2025-12-31/EWR_L21_202512E_Matrix.csv")
DEFAULT_OUTPUT = Path("reports/profiling/afs_population_2025-12-31.json")

def describe_numeric(series: pd.Series) -> dict[str, float | int | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"min": None, "median": None, "mean": None, "max": None, "sum": None}
    return {
        "min": float(values.min()),
        "median": float(values.median()),
        "mean": float(values.mean()),
        "max": float(values.max()),
        "sum": int(values.sum()),
    }

def profile_afs_population(population: pd.DataFrame) -> dict[str, Any]:
    missing = REQUIRED_COLUMNS - set(population.columns)
    if missing:
        raise ValueError(f"Population data is missing required columns: {sorted(missing)}")
    normalized = normalize_population_columns(population)
    derived = add_population_65plus(normalized)
    incomplete = derived.loc[
        derived["population_65plus"].isna(),
        ["RAUMID", "E_E65U80", "E_E80U110"],
    ].copy()
    incomplete = incomplete.astype(object).where(pd.notna(incomplete), None)

    return {
        "dataset": "afs_berlin_lor_population",
        "row_count": int(len(derived)),
        "column_count": int(len(derived.columns)),
        "schema": {
            "columns": list(derived.columns),
            "dtypes": {c: str(t) for c, t in derived.dtypes.items()},
        },
        "identity": {
            "distinct_raumid_count": int(derived["RAUMID"].nunique(dropna=True)),
            "duplicate_raumid_count": int(derived["RAUMID"].duplicated().sum()),
            "null_raumid_count": int(derived["RAUMID"].isna().sum()),
        },
        "reference": {
            "distinct_zeit_count": int(derived["ZEIT"].nunique(dropna=True)),
            "zeit_values": sorted(derived["ZEIT"].dropna().astype(str).unique().tolist()),
        },
        "null_counts": {
            c: int(derived[c].isna().sum())
            for c in ["E_E", "E_E65U80", "E_E80U110", "population_65plus", "share_65plus"]
        },
        "population_total": describe_numeric(derived["E_E"]),
        "population_65_79": describe_numeric(derived["E_E65U80"]),
        "population_80plus": describe_numeric(derived["E_E80U110"]),
        "population_65plus": describe_numeric(derived["population_65plus"]),
        "share_65plus": describe_numeric(derived["share_65plus"]),
        "rows_with_incomplete_65plus": incomplete.to_dict(orient="records"),
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    population = pd.read_csv(args.input_file, sep=";", dtype=str)
    profile = profile_afs_population(population)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

    print("AfS population profile")
    print("----------------------")
    print(f"Rows:                     {profile['row_count']:,}")
    print(f"Distinct RAUMID:          {profile['identity']['distinct_raumid_count']:,}")
    print(f"Duplicate RAUMID:         {profile['identity']['duplicate_raumid_count']:,}")
    print(f"ZEIT values:              {profile['reference']['zeit_values']}")
    print(f"Missing E_E:              {profile['null_counts']['E_E']:,}")
    print(f"Missing E_E65U80:         {profile['null_counts']['E_E65U80']:,}")
    print(f"Missing E_E80U110:        {profile['null_counts']['E_E80U110']:,}")
    print(f"Incomplete age 65+:       {profile['null_counts']['population_65plus']:,}")
    print(f"Total population sum:     {profile['population_total']['sum']:,}")
    print(f"Known population 65+ sum: {profile['population_65plus']['sum']:,}")
    print(f"Profile written to:       {args.output_file}")

if __name__ == "__main__":
    main()
