from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.validate_icon_t2m import (
    MISSING_TEMPERATURE_C,
    MISSING_TEMPERATURE_K,
    REQUIRED_COLUMNS,
    temperature_missing_mask,
)


DEFAULT_T2M_ROOT = Path("data/silver/icon-d2-t2m")
DEFAULT_BRIDGE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)
DEFAULT_OUTPUT_DIR = Path("reports/profiling")


def find_latest_t2m_file(root: Path) -> Path:
    """
    Find the latest decoded T_2M parquet by parent directory name.

    Expected layout:
      data/silver/icon-d2-t2m/<run>/t2m.parquet
    """
    candidates = sorted(
        root.glob("*/t2m.parquet"),
        key=lambda p: p.parent.name,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No t2m.parquet files found below: {root}"
        )

    return candidates[-1]


def describe_numeric(series: pd.Series) -> dict[str, float | None]:
    """Return a compact numeric profile for observed values."""
    values = pd.to_numeric(
        series,
        errors="coerce",
    )

    if values.dropna().empty:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "std": None,
        }

    return {
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "std": float(values.std()),
    }


def profile_t2m(
    t2m: pd.DataFrame,
    *,
    bridge: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Profile one decoded ICON-D2-RUC T_2M run.

    Known ICON missing-temperature sentinels are excluded from temperature
    distributions and reported explicitly as missing observations.
    """
    missing_columns = REQUIRED_COLUMNS - set(t2m.columns)
    if missing_columns:
        raise ValueError(
            "T_2M data is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    temp_k = pd.to_numeric(
        t2m["temperature_k"],
        errors="coerce",
    )
    temp_c = pd.to_numeric(
        t2m["temperature_c"],
        errors="coerce",
    )

    missing_mask = temperature_missing_mask(t2m)
    observed_mask = ~missing_mask

    observed_k = temp_k.loc[observed_mask].replace(
        [np.inf, -np.inf],
        np.nan,
    )
    observed_c = temp_c.loc[observed_mask].replace(
        [np.inf, -np.inf],
        np.nan,
    )

    run_times = pd.to_datetime(
        t2m["run_time_utc"],
        utc=True,
        errors="coerce",
    )
    valid_times = pd.to_datetime(
        t2m["valid_time_utc"],
        utc=True,
        errors="coerce",
    )

    profile: dict[str, Any] = {
        "dataset": "dwd_icon_d2_ruc_t2m",
        "row_count": int(len(t2m)),
        "distinct_cell_count": int(
            t2m["cell_index"].nunique(dropna=True)
        ),
        "duplicate_cell_index_count": int(
            t2m["cell_index"].duplicated().sum()
        ),
        "null_counts": {
            column: int(t2m[column].isna().sum())
            for column in sorted(REQUIRED_COLUMNS)
        },
        "run_time_utc": (
            run_times.dropna().iloc[0].isoformat()
            if run_times.notna().any()
            else None
        ),
        "valid_time_utc": (
            valid_times.dropna().iloc[0].isoformat()
            if valid_times.notna().any()
            else None
        ),
        "distinct_run_time_count": int(
            run_times.nunique(dropna=True)
        ),
        "distinct_valid_time_count": int(
            valid_times.nunique(dropna=True)
        ),
        "missing_temperature": {
            "sentinel_temperature_k": MISSING_TEMPERATURE_K,
            "sentinel_temperature_c": MISSING_TEMPERATURE_C,
            "missing_row_count": int(missing_mask.sum()),
            "missing_share": (
                float(missing_mask.mean())
                if len(t2m) > 0
                else 0.0
            ),
        },
        "observed_temperature_k": describe_numeric(
            observed_k
        ),
        "observed_temperature_c": describe_numeric(
            observed_c
        ),
        "non_finite_observed_temperature_k_count": int(
            (~np.isfinite(
                observed_k.dropna().to_numpy(dtype="float64")
            )).sum()
        ),
        "non_finite_observed_temperature_c_count": int(
            (~np.isfinite(
                observed_c.dropna().to_numpy(dtype="float64")
            )).sum()
        ),
    }

    if (
        profile["run_time_utc"] is not None
        and profile["valid_time_utc"] is not None
    ):
        run_time = pd.Timestamp(profile["run_time_utc"])
        valid_time = pd.Timestamp(profile["valid_time_utc"])
        profile["lead_time_minutes"] = int(
            (valid_time - run_time).total_seconds() / 60
        )

    if bridge is not None:
        if "cell_index" not in bridge.columns:
            raise ValueError(
                "Bridge data must contain cell_index"
            )

        relevant_cell_ids = (
            bridge["cell_index"]
            .dropna()
            .drop_duplicates()
        )

        berlin = t2m[
            t2m["cell_index"].isin(relevant_cell_ids)
        ].copy()

        berlin_missing_mask = temperature_missing_mask(
            berlin
        )

        berlin_observed = berlin.loc[
            ~berlin_missing_mask
        ]

        profile["berlin_intersection_subset"] = {
            "expected_relevant_cell_count": int(
                relevant_cell_ids.nunique()
            ),
            "matched_cell_count": int(
                berlin["cell_index"].nunique()
            ),
            "missing_relevant_cell_count": int(
                relevant_cell_ids.nunique()
                - berlin["cell_index"].nunique()
            ),
            "missing_temperature_row_count": int(
                berlin_missing_mask.sum()
            ),
            "observed_temperature_c": (
                describe_numeric(
                    berlin_observed["temperature_c"]
                )
                if not berlin_observed.empty
                else None
            ),
        }

    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile one decoded ICON-D2-RUC T_2M run and "
            "persist the results as JSON."
        )
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help=(
            "Decoded t2m.parquet. If omitted, use the latest "
            f"file below {DEFAULT_T2M_ROOT}."
        ),
    )
    parser.add_argument(
        "--bridge-file",
        type=Path,
        default=DEFAULT_BRIDGE,
        help=(
            "Optional ICON↔PLR bridge used to profile the Berlin "
            "intersection subset."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Profile directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    args = parser.parse_args()

    input_file = (
        args.input_file
        if args.input_file is not None
        else find_latest_t2m_file(DEFAULT_T2M_ROOT)
    )

    if not input_file.exists():
        raise FileNotFoundError(
            f"T_2M input file not found: {input_file}"
        )

    print(f"Reading T_2M: {input_file}")
    t2m = pd.read_parquet(input_file)

    bridge = None
    if args.bridge_file.exists():
        print(f"Reading bridge: {args.bridge_file}")
        bridge = pd.read_parquet(args.bridge_file)

    profile = profile_t2m(
        t2m,
        bridge=bridge,
    )

    run_label = input_file.parent.name
    output_file = (
        args.output_dir
        / f"icon_t2m_{run_label}.json"
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_file.write_text(
        json.dumps(
            profile,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("ICON T_2M profile")
    print("----------------")
    print(f"Rows:                   {profile['row_count']:,}")
    print(
        f"Distinct cells:         "
        f"{profile['distinct_cell_count']:,}"
    )
    print(
        f"Missing temperatures:   "
        f"{profile['missing_temperature']['missing_row_count']:,}"
    )
    print(
        f"Run time:               "
        f"{profile['run_time_utc']}"
    )
    print(
        f"Valid time:             "
        f"{profile['valid_time_utc']}"
    )
    print(
        f"Lead time (minutes):    "
        f"{profile.get('lead_time_minutes')}"
    )

    observed = profile["observed_temperature_c"]
    if observed["min"] is not None:
        print(
            f"Observed temperature:   "
            f"{observed['min']:.2f} – "
            f"{observed['max']:.2f} °C"
        )
    else:
        print("Observed temperature:   no observed values")

    berlin = profile.get("berlin_intersection_subset")
    if berlin is not None:
        print(
            f"Berlin cells matched:   "
            f"{berlin['matched_cell_count']:,} / "
            f"{berlin['expected_relevant_cell_count']:,}"
        )
        print(
            f"Berlin missing temps:   "
            f"{berlin['missing_temperature_row_count']:,}"
        )

    print(f"Profile written to:     {output_file}")


if __name__ == "__main__":
    main()
