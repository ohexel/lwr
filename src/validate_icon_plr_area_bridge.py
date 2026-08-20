from pathlib import Path

import numpy as np
import pandas as pd


ICON_PLR_AREA_BRIDGE_PATH = Path(
    "data/normalized/icon_d2_grid/"
    "icon_plr_area_bridge.parquet"
)

EXPECTED_PLR_COUNT = 542
WEIGHT_TOLERANCE = 1e-5

REQUIRED_BRIDGE_COLUMNS = {
    "plr_id",
    "cell_index",
    "intersection_area_m2",
    "plr_area_m2",
    "icon_cell_area_m2",
    "fraction_of_plr",
    "fraction_of_icon_cell",
}


def validate_icon_plr_area_bridge(
    bridge: pd.DataFrame,
    *,
    expected_plr_count: int = EXPECTED_PLR_COUNT,
    weight_tolerance: float = WEIGHT_TOLERANCE,
) -> dict:
    """
    Validate the persisted ICON↔PLR area bridge and return compact
    descriptive metadata.

    The key spatial contract is complete PLR coverage with
    fraction_of_plr summing to approximately one for every PLR.
    """
    missing_columns = (
        REQUIRED_BRIDGE_COLUMNS
        - set(bridge.columns)
    )
    if missing_columns:
        raise ValueError(
            "ICON↔PLR bridge missing required columns: "
            f"{sorted(missing_columns)}"
        )

    if bridge.empty:
        raise ValueError(
            "ICON↔PLR bridge is empty"
        )

    if bridge[
        ["plr_id", "cell_index"]
    ].duplicated().any():
        raise ValueError(
            "ICON↔PLR bridge contains duplicate "
            "(plr_id, cell_index) pairs"
        )

    numeric_columns = [
        "intersection_area_m2",
        "plr_area_m2",
        "icon_cell_area_m2",
        "fraction_of_plr",
        "fraction_of_icon_cell",
    ]

    for column in numeric_columns:
        values = bridge[column].to_numpy(
            dtype="float64"
        )

        if not np.isfinite(values).all():
            raise ValueError(
                f"ICON↔PLR bridge column "
                f"{column} contains non-finite values"
            )

    if (
        bridge["intersection_area_m2"]
        <= 0
    ).any():
        raise ValueError(
            "ICON↔PLR bridge contains non-positive "
            "intersection areas"
        )

    if (
        bridge["fraction_of_plr"]
        <= 0
    ).any():
        raise ValueError(
            "ICON↔PLR bridge contains non-positive "
            "fraction_of_plr values"
        )

    if (
        bridge["fraction_of_plr"]
        > 1 + weight_tolerance
    ).any():
        raise ValueError(
            "ICON↔PLR bridge contains "
            "fraction_of_plr values above 1 "
            "outside floating-point tolerance"
        )

    plr_ids = (
        bridge["plr_id"]
        .astype("string")
        .drop_duplicates()
    )

    plr_count = len(plr_ids)

    if plr_count != expected_plr_count:
        raise ValueError(
            "ICON↔PLR bridge must cover "
            f"{expected_plr_count} PLRs; "
            f"got {plr_count}"
        )

    weight_sums = (
        bridge.groupby("plr_id")[
            "fraction_of_plr"
        ]
        .sum()
        .astype("float64")
    )

    deviations = (
        weight_sums - 1.0
    ).abs()

    bad = deviations[
        deviations > weight_tolerance
    ]

    if not bad.empty:
        examples = {
            str(plr_id): float(
                weight_sums.loc[plr_id]
            )
            for plr_id in bad.index[:10]
        }

        raise ValueError(
            "fraction_of_plr must sum to "
            "approximately 1 for every PLR; "
            f"examples={examples}"
        )

    return {
        "row_count": int(len(bridge)),
        "plr_count": int(plr_count),
        "icon_cell_count": int(
            bridge["cell_index"].nunique()
        ),
        "min_fraction_of_plr_sum": float(
            weight_sums.min()
        ),
        "max_fraction_of_plr_sum": float(
            weight_sums.max()
        ),
        "max_fraction_of_plr_deviation": float(
            deviations.max()
        ),
    }


def read_and_validate_icon_plr_area_bridge(
    path: Path = ICON_PLR_AREA_BRIDGE_PATH,
) -> tuple[pd.DataFrame, dict]:
    if not path.exists():
        raise FileNotFoundError(
            "Canonical ICON↔PLR area bridge "
            f"does not exist: {path}"
        )

    bridge = pd.read_parquet(path)

    metadata = (
        validate_icon_plr_area_bridge(
            bridge
        )
    )

    return bridge, metadata
