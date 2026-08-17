from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_BRIDGE_FILE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)

DEFAULT_T2M_ROOT = Path(
    "data/silver/icon-d2-t2m"
)

DEFAULT_OUTPUT_ROOT = Path(
    "data/gold/plr-temperature"
)


def find_latest_t2m_file(root: Path) -> Path:
    files = sorted(root.glob("*/t2m.parquet"))

    if not files:
        raise FileNotFoundError(
            f"No t2m.parquet files found below {root}"
        )

    return files[-1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate area-weighted ICON-D2 2m air temperature "
            "for each Berlin LOR Planungsraum."
        )
    )
    parser.add_argument(
        "--bridge-file",
        type=Path,
        default=DEFAULT_BRIDGE_FILE,
    )
    parser.add_argument(
        "--t2m-file",
        type=Path,
        help=(
            "Specific silver t2m.parquet file. "
            "Defaults to latest run below data/silver/icon-d2-t2m."
        ),
    )
    parser.add_argument(
        "--t2m-root",
        type=Path,
        default=DEFAULT_T2M_ROOT,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )

    args = parser.parse_args()

    t2m_file = args.t2m_file or find_latest_t2m_file(
        args.t2m_root
    )

    if not args.bridge_file.exists():
        raise FileNotFoundError(
            f"Missing ICON↔PLR bridge: {args.bridge_file}"
        )

    if not t2m_file.exists():
        raise FileNotFoundError(
            f"Missing T_2M silver file: {t2m_file}"
        )

    print(f"Reading bridge: {args.bridge_file}")
    bridge = pd.read_parquet(args.bridge_file)

    print(f"Reading T_2M:   {t2m_file}")
    t2m = pd.read_parquet(t2m_file)

    bridge_required = {
        "plr_id",
        "cell_index",
        "fraction_of_plr",
    }

    t2m_required = {
        "cell_index",
        "run_time_utc",
        "valid_time_utc",
        "temperature_k",
        "temperature_c",
    }

    missing_bridge = bridge_required - set(bridge.columns)
    missing_t2m = t2m_required - set(t2m.columns)

    if missing_bridge:
        raise ValueError(
            "Bridge is missing required columns: "
            f"{sorted(missing_bridge)}"
        )

    if missing_t2m:
        raise ValueError(
            "T_2M file is missing required columns: "
            f"{sorted(missing_t2m)}"
        )

    relevant_cells = set(
        bridge["cell_index"].astype(int).unique()
    )

    available_cells = set(
        t2m["cell_index"].astype(int).unique()
    )

    missing_cells = relevant_cells - available_cells

    print()
    print(f"PLRs in bridge:              {bridge['plr_id'].nunique():,}")
    print(f"ICON cells needed:           {len(relevant_cells):,}")
    print(f"ICON cells in T_2M dataset:  {len(available_cells):,}")

    if missing_cells:
        sample = sorted(missing_cells)[:10]
        raise RuntimeError(
            f"T_2M is missing {len(missing_cells)} ICON cells "
            f"required by the Berlin bridge. "
            f"Example missing cells: {sample}"
        )

    # Restrict the large weather field to Berlin-relevant cells before join.
    t2m_berlin = t2m[
        t2m["cell_index"].isin(relevant_cells)
    ].copy()

    # There should be exactly one T_2M value per cell for one run.
    duplicate_cells = t2m_berlin[
        t2m_berlin.duplicated(
            subset=["cell_index"],
            keep=False,
        )
    ]

    if not duplicate_cells.empty:
        raise RuntimeError(
            "T_2M input contains duplicate cell_index values "
            "for this run."
        )

    joined = bridge.merge(
        t2m_berlin,
        on="cell_index",
        how="left",
        validate="many_to_one",
    )

    if joined["temperature_c"].isna().any():
        missing_rows = joined[
            joined["temperature_c"].isna()
        ]

        raise RuntimeError(
            "Some PLR/ICON intersections have no temperature. "
            f"Missing rows: {len(missing_rows):,}"
        )

    joined["weighted_temperature_c"] = (
        joined["temperature_c"]
        * joined["fraction_of_plr"]
    )

    joined["weighted_temperature_k"] = (
        joined["temperature_k"]
        * joined["fraction_of_plr"]
    )

    # Sum the weighted contributions from all ICON triangles
    # intersecting each PLR.
    plr_temperature = (
        joined.groupby(
            "plr_id",
            as_index=False,
        )
        .agg(
            temperature_c=(
                "weighted_temperature_c",
                "sum",
            ),
            temperature_k=(
                "weighted_temperature_k",
                "sum",
            ),
            weight_sum=(
                "fraction_of_plr",
                "sum",
            ),
            icon_cells_used=(
                "cell_index",
                "nunique",
            ),
        )
    )

    # One run / valid time is expected in this input file.
    run_times = t2m_berlin["run_time_utc"].dropna().unique()
    valid_times = t2m_berlin["valid_time_utc"].dropna().unique()

    if len(run_times) != 1:
        raise RuntimeError(
            "Expected exactly one run_time_utc in t2m.parquet, "
            f"found {len(run_times)}."
        )

    if len(valid_times) != 1:
        raise RuntimeError(
            "Expected exactly one valid_time_utc in t2m.parquet, "
            f"found {len(valid_times)}."
        )

    run_time = pd.Timestamp(run_times[0])
    valid_time = pd.Timestamp(valid_times[0])

    plr_temperature["run_time_utc"] = run_time
    plr_temperature["valid_time_utc"] = valid_time

    plr_temperature = plr_temperature[
        [
            "plr_id",
            "run_time_utc",
            "valid_time_utc",
            "temperature_c",
            "temperature_k",
            "icon_cells_used",
            "weight_sum",
        ]
    ].sort_values("plr_id")

    # Validate that the spatial weights still cover every PLR.
    tolerance = 1e-6

    bad_weights = plr_temperature[
        (plr_temperature["weight_sum"] - 1.0).abs()
        > tolerance
    ]

    if not bad_weights.empty:
        raise RuntimeError(
            "PLR weights do not sum to 1 within tolerance. "
            f"Affected PLRs: {len(bad_weights)}"
        )

    run_label = run_time.strftime("%Y-%m-%dT%H%M")

    output_dir = args.output_root / run_label
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = output_dir / "plr_temperature.parquet"

    plr_temperature.to_parquet(
        output_file,
        index=False,
    )

    print()
    print("Area-weighted PLR temperature complete")
    print("--------------------------------------")
    print(f"Run time:             {run_time}")
    print(f"Valid time:           {valid_time}")
    print(f"PLRs produced:        {len(plr_temperature):,}")
    print(
        f"Min temperature:      "
        f"{plr_temperature['temperature_c'].min():.2f} °C"
    )
    print(
        f"Mean temperature:     "
        f"{plr_temperature['temperature_c'].mean():.2f} °C"
    )
    print(
        f"Max temperature:      "
        f"{plr_temperature['temperature_c'].max():.2f} °C"
    )
    print(
        f"Min weight sum:       "
        f"{plr_temperature['weight_sum'].min():.6f}"
    )
    print(
        f"Max weight sum:       "
        f"{plr_temperature['weight_sum'].max():.6f}"
    )
    print(f"Output:               {output_file}")


if __name__ == "__main__":
    main()
