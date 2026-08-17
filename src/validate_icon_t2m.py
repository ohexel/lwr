from __future__ import annotations

import numpy as np
import pandas as pd


EXPECTED_ICON_CELL_COUNT = 542_040

MISSING_TEMPERATURE_K = 9999.0
MISSING_TEMPERATURE_C = 9725.85

MIN_TEMPERATURE_K = 253.0
MAX_TEMPERATURE_K = 313.0

REQUIRED_COLUMNS = {
    "cell_index",
    "run_time_utc",
    "valid_time_utc",
    "temperature_k",
    "temperature_c",
}


def kelvin_to_celsius(value):
    """Convert Kelvin to degrees Celsius."""
    return value - 273.15


def temperature_missing_mask(t2m: pd.DataFrame) -> pd.Series:
    """
    Return rows that use the ICON missing-temperature sentinels.

    The decoded dataset represents missing temperature values as:
      temperature_k == 9999
      temperature_c == 9726
    """
    temp_k = pd.to_numeric(
        t2m["temperature_k"],
        errors="coerce",
    )
    temp_c = pd.to_numeric(
        t2m["temperature_c"],
        errors="coerce",
    )

    return (
        temp_k.eq(MISSING_TEMPERATURE_K)
        & temp_c.eq(MISSING_TEMPERATURE_C)
    )


def validate_t2m_contract(
    t2m: pd.DataFrame,
    *,
    expected_cell_count: int | None = EXPECTED_ICON_CELL_COUNT,
) -> None:
    """
    Validate structural and semantic contracts for one decoded T_2M run.

    Missing temperature sentinels (9999 K / 9726 °C) are accepted as known
    source semantics. All other temperature rows must be finite, internally
    consistent, and inside the project's broad Kelvin sanity bounds.
    """
    missing_columns = REQUIRED_COLUMNS - set(t2m.columns)
    if missing_columns:
        raise ValueError(
            "T_2M data is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    if expected_cell_count is not None and len(t2m) != expected_cell_count:
        raise ValueError(
            "Unexpected T_2M row count: "
            f"{len(t2m):,}; expected {expected_cell_count:,}"
        )

    if t2m["cell_index"].isna().any():
        raise ValueError("T_2M contains null cell_index values")

    if not t2m["cell_index"].is_unique:
        raise ValueError("T_2M contains duplicate cell_index values")

    if t2m["run_time_utc"].isna().any():
        raise ValueError("T_2M contains null run_time_utc values")

    if t2m["valid_time_utc"].isna().any():
        raise ValueError("T_2M contains null valid_time_utc values")

    if t2m["run_time_utc"].nunique(dropna=False) != 1:
        raise ValueError(
            "T_2M file contains more than one run_time_utc"
        )

    if t2m["valid_time_utc"].nunique(dropna=False) != 1:
        raise ValueError(
            "T_2M file contains more than one valid_time_utc"
        )

    temp_k = pd.to_numeric(
        t2m["temperature_k"],
        errors="coerce",
    )
    temp_c = pd.to_numeric(
        t2m["temperature_c"],
        errors="coerce",
    )

    # Missing values must use the two source sentinels together.
    k_is_missing = temp_k.eq(MISSING_TEMPERATURE_K)
    c_is_missing = temp_c.eq(MISSING_TEMPERATURE_C)

    mismatched_missing = k_is_missing ^ c_is_missing
    if mismatched_missing.any():
        raise ValueError(
            "Temperature missing-value sentinels are inconsistent: "
            f"{int(mismatched_missing.sum())} rows use only one of "
            f"{MISSING_TEMPERATURE_K:g} K / {MISSING_TEMPERATURE_C:g} °C"
        )

    missing_mask = k_is_missing & c_is_missing
    observed_mask = ~missing_mask

    observed_k = temp_k.loc[observed_mask]
    observed_c = temp_c.loc[observed_mask]

    if not np.isfinite(
        observed_k.to_numpy(dtype="float64")
    ).all():
        raise ValueError(
            "T_2M contains non-finite observed temperature_k values"
        )

    if not np.isfinite(
        observed_c.to_numpy(dtype="float64")
    ).all():
        raise ValueError(
            "T_2M contains non-finite observed temperature_c values"
        )

    expected_c = kelvin_to_celsius(
        observed_k.to_numpy(dtype="float64")
    )
    actual_c = observed_c.to_numpy(dtype="float64")

    if not np.allclose(
        actual_c,
        expected_c,
        atol=1e-6,
        rtol=0.0,
    ):
        raise ValueError(
            "Observed temperature_c is inconsistent with temperature_k"
        )

    if not observed_k.empty:
        observed_min = float(observed_k.min())
        observed_max = float(observed_k.max())

        if observed_min < MIN_TEMPERATURE_K:
            raise ValueError(
                "Observed temperature below lower Kelvin sanity bound: "
                f"{observed_min:.2f} K < {MIN_TEMPERATURE_K:.2f} K"
            )

        if observed_max > MAX_TEMPERATURE_K:
            raise ValueError(
                "Observed temperature above upper Kelvin sanity bound: "
                f"{observed_max:.2f} K > {MAX_TEMPERATURE_K:.2f} K"
            )
