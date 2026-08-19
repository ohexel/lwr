from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.profile_icon_t2m import (
    find_latest_t2m_file,
    profile_t2m,
)
from src.validate_icon_t2m import (
    EXPECTED_ICON_CELL_COUNT,
    MAX_TEMPERATURE_K,
    MIN_TEMPERATURE_K,
    MISSING_TEMPERATURE_C,
    MISSING_TEMPERATURE_K,
    kelvin_to_celsius,
    temperature_missing_mask,
    validate_t2m_contract,
)


T2M_ROOT = Path("data/silver/icon-d2-t2m")
GRID_CELLS_FILE = Path(
    "data/silver/icon-d2-grid/cells.parquet"
)
BRIDGE_FILE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)


def make_valid_t2m() -> pd.DataFrame:
    temperature_k = np.array(
        [273.15, 280.15, 290.15],
        dtype="float64",
    )

    return pd.DataFrame(
        {
            "cell_index": [0, 1, 2],
            "run_time_utc": pd.to_datetime(
                [
                    "2026-08-13T16:00:00Z",
                    "2026-08-13T16:00:00Z",
                    "2026-08-13T16:00:00Z",
                ],
                utc=True,
            ),
            "valid_time_utc": pd.to_datetime(
                [
                    "2026-08-13T16:00:00Z",
                    "2026-08-13T16:00:00Z",
                    "2026-08-13T16:00:00Z",
                ],
                utc=True,
            ),
            "temperature_k": temperature_k,
            "temperature_c": kelvin_to_celsius(
                temperature_k
            ),
        }
    )


# ---------------------------------------------------------------------------
# Unit tests: validation logic
# ---------------------------------------------------------------------------

def test_kelvin_to_celsius():
    assert kelvin_to_celsius(273.15) == pytest.approx(0.0)
    assert kelvin_to_celsius(293.15) == pytest.approx(20.0)


def test_temperature_missing_mask_recognizes_icon_sentinels():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MISSING_TEMPERATURE_K
    t2m.loc[1, "temperature_c"] = MISSING_TEMPERATURE_C

    mask = temperature_missing_mask(t2m)

    assert mask.tolist() == [False, True, False]


def test_validate_t2m_contract_accepts_missing_sentinel_pair():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MISSING_TEMPERATURE_K
    t2m.loc[1, "temperature_c"] = MISSING_TEMPERATURE_C

    validate_t2m_contract(
        t2m,
        expected_cell_count=3,
    )


def test_validate_t2m_contract_rejects_mismatched_missing_sentinels():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MISSING_TEMPERATURE_K

    with pytest.raises(
        ValueError,
        match="sentinels are inconsistent",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


def test_validate_t2m_contract_rejects_duplicate_cell_index():
    t2m = make_valid_t2m()
    t2m.loc[2, "cell_index"] = 1

    with pytest.raises(
        ValueError,
        match="duplicate cell_index",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


def test_validate_t2m_contract_rejects_non_finite_observed_kelvin():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = np.inf
    t2m.loc[1, "temperature_c"] = np.inf

    with pytest.raises(
        ValueError,
        match="non-finite observed temperature_k",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


def test_validate_t2m_contract_rejects_temperature_above_upper_bound():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MAX_TEMPERATURE_K + 1
    t2m.loc[1, "temperature_c"] = kelvin_to_celsius(
        MAX_TEMPERATURE_K + 1
    )

    with pytest.raises(
        ValueError,
        match="above upper Kelvin sanity bound",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


def test_validate_t2m_contract_rejects_temperature_below_lower_bound():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MIN_TEMPERATURE_K - 1
    t2m.loc[1, "temperature_c"] = kelvin_to_celsius(
        MIN_TEMPERATURE_K - 1
    )

    with pytest.raises(
        ValueError,
        match="below lower Kelvin sanity bound",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


def test_validate_t2m_contract_rejects_inconsistent_celsius():
    t2m = make_valid_t2m()
    t2m.loc[0, "temperature_c"] = 99.0

    with pytest.raises(
        ValueError,
        match="inconsistent with temperature_k",
    ):
        validate_t2m_contract(
            t2m,
            expected_cell_count=3,
        )


# ---------------------------------------------------------------------------
# Unit tests: profiling logic
# ---------------------------------------------------------------------------

def test_profile_t2m_excludes_missing_sentinels_from_distribution():
    t2m = make_valid_t2m()
    t2m.loc[1, "temperature_k"] = MISSING_TEMPERATURE_K
    t2m.loc[1, "temperature_c"] = MISSING_TEMPERATURE_C

    profile = profile_t2m(t2m)

    assert profile["missing_temperature"]["missing_row_count"] == 1
    assert profile["observed_temperature_k"]["max"] == pytest.approx(
        290.15
    )
    assert profile["observed_temperature_c"]["max"] == pytest.approx(
        17.0
    )


def test_profile_t2m_reports_lead_zero():
    t2m = make_valid_t2m()

    profile = profile_t2m(t2m)

    assert profile["row_count"] == 3
    assert profile["distinct_cell_count"] == 3
    assert profile["lead_time_minutes"] == 0


def test_profile_t2m_reports_berlin_missing_temperature():
    t2m = make_valid_t2m()
    t2m.loc[2, "temperature_k"] = MISSING_TEMPERATURE_K
    t2m.loc[2, "temperature_c"] = MISSING_TEMPERATURE_C

    bridge = pd.DataFrame(
        {
            "plr_id": ["A", "A"],
            "cell_index": [0, 2],
        }
    )

    profile = profile_t2m(
        t2m,
        bridge=bridge,
    )

    berlin = profile["berlin_intersection_subset"]

    assert berlin["expected_relevant_cell_count"] == 2
    assert berlin["matched_cell_count"] == 2
    assert berlin["missing_temperature_row_count"] == 1


# ---------------------------------------------------------------------------
# Real-data contract tests: latest decoded T_2M run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_t2m_file() -> Path:
    if not T2M_ROOT.exists():
        pytest.skip(
            f"T_2M root not available: {T2M_ROOT}"
        )

    return find_latest_t2m_file(T2M_ROOT)


@pytest.fixture(scope="module")
def real_t2m(
    real_t2m_file: Path,
) -> pd.DataFrame:
    return pd.read_parquet(real_t2m_file)


def test_real_t2m_contract(
    real_t2m: pd.DataFrame,
):
    validate_t2m_contract(
        real_t2m,
        expected_cell_count=EXPECTED_ICON_CELL_COUNT,
    )


def test_real_t2m_is_lead_zero(
    real_t2m: pd.DataFrame,
):
    run_time = pd.to_datetime(
        real_t2m["run_time_utc"],
        utc=True,
    ).iloc[0]

    valid_time = pd.to_datetime(
        real_t2m["valid_time_utc"],
        utc=True,
    ).iloc[0]

    assert valid_time == run_time


def test_real_t2m_cell_indexes_match_decoded_grid(
    real_t2m: pd.DataFrame,
):
    if not GRID_CELLS_FILE.exists():
        pytest.skip(
            f"Decoded grid not available: {GRID_CELLS_FILE}"
        )

    grid = pd.read_parquet(
        GRID_CELLS_FILE,
        columns=["cell_index"],
    )

    assert len(grid) == len(real_t2m)
    assert grid["cell_index"].is_unique
    assert set(grid["cell_index"]) == set(
        real_t2m["cell_index"]
    )


def test_real_t2m_contains_all_bridge_cells(
    real_t2m: pd.DataFrame,
):
    if not BRIDGE_FILE.exists():
        pytest.skip(
            f"ICON↔PLR bridge not available: {BRIDGE_FILE}"
        )

    bridge = pd.read_parquet(
        BRIDGE_FILE,
        columns=["cell_index"],
    )

    required_cells = set(
        bridge["cell_index"].dropna().unique()
    )
    available_cells = set(
        real_t2m["cell_index"].dropna().unique()
    )

    missing = required_cells - available_cells

    assert not missing, (
        f"{len(missing)} bridge cells are missing "
        "from the T_2M field"
    )


def test_real_t2m_profile_does_not_treat_sentinels_as_observations(
    real_t2m: pd.DataFrame,
):
    profile = profile_t2m(real_t2m)

    observed_k = profile["observed_temperature_k"]
    observed_c = profile["observed_temperature_c"]

    assert observed_k["max"] != MISSING_TEMPERATURE_K
    assert observed_c["max"] != MISSING_TEMPERATURE_C
