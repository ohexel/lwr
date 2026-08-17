from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_BRIDGE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)
DEFAULT_LOR = Path(
    "data/raw/berlin/lor/lor_planungsraum.geojson"
)
DEFAULT_ICON_CELLS = Path(
    "data/silver/icon-d2-grid/cells.parquet"
)
DEFAULT_OUTPUT = Path(
    "reports/profiling/icon_plr_bridge_profile.json"
)

REQUIRED_COLUMNS = {
    "plr_id",
    "cell_index",
    "intersection_area_m2",
    "plr_area_m2",
    "icon_cell_area_m2",
    "fraction_of_plr",
    "fraction_of_icon_cell",
}


def describe_numeric(series: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(
        series,
        errors="coerce",
    ).dropna()

    if values.empty:
        return {
            "min": None,
            "median": None,
            "mean": None,
            "max": None,
        }

    return {
        "min": float(values.min()),
        "median": float(values.median()),
        "mean": float(values.mean()),
        "max": float(values.max()),
    }


def profile_icon_plr_bridge(
    bridge: pd.DataFrame,
    *,
    lor_ids: pd.Series | None = None,
    icon_cell_ids: pd.Series | None = None,
) -> dict[str, Any]:
    """
    Profile the derived ICON↔PLR area-weight bridge.

    Profiling records descriptive and coverage metrics. It does not itself
    enforce contracts; those belong in tests/runtime validation.
    """
    missing_columns = REQUIRED_COLUMNS - set(bridge.columns)
    if missing_columns:
        raise ValueError(
            "Bridge is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    per_plr = (
        bridge.groupby("plr_id", dropna=False)
        .agg(
            icon_cells_used=("cell_index", "nunique"),
            weight_sum=("fraction_of_plr", "sum"),
            intersection_area_sum_m2=(
                "intersection_area_m2",
                "sum",
            ),
            plr_area_m2=("plr_area_m2", "first"),
        )
        .reset_index()
    )

    per_plr["coverage_ratio"] = (
        per_plr["intersection_area_sum_m2"]
        / per_plr["plr_area_m2"]
    )

    profile: dict[str, Any] = {
        "dataset": "icon_d2_plr_area_bridge",
        "row_count": int(len(bridge)),
        "distinct_plr_count": int(
            bridge["plr_id"].nunique(dropna=True)
        ),
        "distinct_icon_cell_count": int(
            bridge["cell_index"].nunique(dropna=True)
        ),
        "null_counts": {
            column: int(bridge[column].isna().sum())
            for column in sorted(REQUIRED_COLUMNS)
        },
        "duplicate_plr_cell_pair_count": int(
            bridge.duplicated(
                subset=["plr_id", "cell_index"]
            ).sum()
        ),
        "intersection_area_m2": describe_numeric(
            bridge["intersection_area_m2"]
        ),
        "fraction_of_plr": describe_numeric(
            bridge["fraction_of_plr"]
        ),
        "fraction_of_icon_cell": describe_numeric(
            bridge["fraction_of_icon_cell"]
        ),
        "icon_cells_per_plr": describe_numeric(
            per_plr["icon_cells_used"]
        ),
        "plr_weight_sum": describe_numeric(
            per_plr["weight_sum"]
        ),
        "plr_area_coverage_ratio": describe_numeric(
            per_plr["coverage_ratio"]
        ),
        "plr_weight_sum_max_abs_error_from_1": float(
            np.max(
                np.abs(
                    per_plr["weight_sum"].to_numpy(
                        dtype="float64"
                    )
                    - 1.0
                )
            )
        ),
        "plr_coverage_max_abs_error_from_1": float(
            np.max(
                np.abs(
                    per_plr["coverage_ratio"].to_numpy(
                        dtype="float64"
                    )
                    - 1.0
                )
            )
        ),
    }

    if lor_ids is not None:
        source_plr_ids = set(
            lor_ids.dropna().astype(str)
        )
        bridge_plr_ids = set(
            bridge["plr_id"].dropna().astype(str)
        )

        missing_from_bridge = (
            source_plr_ids - bridge_plr_ids
        )
        unexpected_in_bridge = (
            bridge_plr_ids - source_plr_ids
        )

        profile["lor_referential_coverage"] = {
            "source_plr_count": int(
                len(source_plr_ids)
            ),
            "bridge_plr_count": int(
                len(bridge_plr_ids)
            ),
            "missing_plr_count": int(
                len(missing_from_bridge)
            ),
            "unexpected_plr_count": int(
                len(unexpected_in_bridge)
            ),
            "missing_plr_ids": sorted(
                missing_from_bridge
            ),
            "unexpected_plr_ids": sorted(
                unexpected_in_bridge
            ),
        }

    if icon_cell_ids is not None:
        source_cells = set(
            pd.to_numeric(
                icon_cell_ids,
                errors="coerce",
            )
            .dropna()
            .astype("int64")
        )
        bridge_cells = set(
            pd.to_numeric(
                bridge["cell_index"],
                errors="coerce",
            )
            .dropna()
            .astype("int64")
        )

        missing_cells = bridge_cells - source_cells

        profile["icon_referential_coverage"] = {
            "source_icon_cell_count": int(
                len(source_cells)
            ),
            "bridge_icon_cell_count": int(
                len(bridge_cells)
            ),
            "bridge_cells_missing_from_source_count": int(
                len(missing_cells)
            ),
            "bridge_cells_missing_from_source": sorted(
                missing_cells
            ),
        }

    return profile


def find_plr_id_column(columns: list[str]) -> str:
    candidates = [
        "plr_id",
        "PLR_ID",
        "RAUMID",
        "raumid",
        "PLR",
        "plr",
    ]

    for candidate in candidates:
        if candidate in columns:
            return candidate

    raise ValueError(
        "Could not identify PLR ID column in LOR dataset. "
        f"Available columns: {columns}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the static ICON-D2 ↔ Berlin PLR "
            "area-weight bridge."
        )
    )
    parser.add_argument(
        "--bridge-file",
        type=Path,
        default=DEFAULT_BRIDGE,
    )
    parser.add_argument(
        "--lor-file",
        type=Path,
        default=DEFAULT_LOR,
    )
    parser.add_argument(
        "--icon-cells-file",
        type=Path,
        default=DEFAULT_ICON_CELLS,
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    if not args.bridge_file.exists():
        raise FileNotFoundError(
            f"Bridge file not found: {args.bridge_file}"
        )

    print(f"Reading bridge: {args.bridge_file}")
    bridge = pd.read_parquet(
        args.bridge_file
    )

    lor_ids = None
    if args.lor_file.exists():
        import geopandas as gpd

        print(f"Reading LOR:    {args.lor_file}")
        lor = gpd.read_file(
            args.lor_file
        )
        plr_id_column = find_plr_id_column(
            list(lor.columns)
        )
        lor_ids = lor[plr_id_column]

    icon_cell_ids = None
    if args.icon_cells_file.exists():
        print(
            f"Reading cells:  "
            f"{args.icon_cells_file}"
        )
        icon_cells = pd.read_parquet(
            args.icon_cells_file,
            columns=["cell_index"],
        )
        icon_cell_ids = icon_cells["cell_index"]

    profile = profile_icon_plr_bridge(
        bridge,
        lor_ids=lor_ids,
        icon_cell_ids=icon_cell_ids,
    )

    args.output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    args.output_file.write_text(
        json.dumps(
            profile,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("ICON ↔ PLR bridge profile")
    print("-------------------------")
    print(
        f"Intersection rows:       "
        f"{profile['row_count']:,}"
    )
    print(
        f"PLRs represented:        "
        f"{profile['distinct_plr_count']:,}"
    )
    print(
        f"ICON cells represented:  "
        f"{profile['distinct_icon_cell_count']:,}"
    )
    print(
        f"Duplicate PLR/cell rows: "
        f"{profile['duplicate_plr_cell_pair_count']:,}"
    )
    print(
        f"Cells per PLR:           "
        f"{profile['icon_cells_per_plr']['min']:.0f} – "
        f"{profile['icon_cells_per_plr']['max']:.0f} "
        f"(mean {profile['icon_cells_per_plr']['mean']:.2f})"
    )
    print(
        f"PLR weight sum range:    "
        f"{profile['plr_weight_sum']['min']:.9f} – "
        f"{profile['plr_weight_sum']['max']:.9f}"
    )
    print(
        f"PLR coverage range:      "
        f"{profile['plr_area_coverage_ratio']['min']:.9f} – "
        f"{profile['plr_area_coverage_ratio']['max']:.9f}"
    )

    lor_coverage = profile.get(
        "lor_referential_coverage"
    )
    if lor_coverage:
        print(
            f"Missing source PLRs:     "
            f"{lor_coverage['missing_plr_count']:,}"
        )

    icon_coverage = profile.get(
        "icon_referential_coverage"
    )
    if icon_coverage:
        print(
            f"Unknown ICON cells:      "
            f"{icon_coverage['bridge_cells_missing_from_source_count']:,}"
        )

    print(
        f"Profile written to:      "
        f"{args.output_file}"
    )


if __name__ == "__main__":
    main()
