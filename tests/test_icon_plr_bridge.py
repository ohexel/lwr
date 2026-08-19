from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon

from src.profile_icon_plr_bridge import (
    find_plr_id_column,
    profile_icon_plr_bridge,
)


BRIDGE_FILE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)
LOR_FILE = Path(
    "data/raw/berlin/lor/lor_planungsraum.geojson"
)
ICON_CELLS_FILE = Path(
    "data/silver/icon-d2-grid/cells.parquet"
)


# ---------------------------------------------------------------------------
# Unit tests: bridge profiling logic
# ---------------------------------------------------------------------------

def test_profile_bridge_known_weights():
    bridge = pd.DataFrame(
        {
            "plr_id": ["A", "A"],
            "cell_index": [10, 11],
            "intersection_area_m2": [25.0, 75.0],
            "plr_area_m2": [100.0, 100.0],
            "icon_cell_area_m2": [100.0, 100.0],
            "fraction_of_plr": [0.25, 0.75],
            "fraction_of_icon_cell": [0.25, 0.75],
        }
    )

    profile = profile_icon_plr_bridge(
        bridge
    )

    assert profile["row_count"] == 2
    assert profile["distinct_plr_count"] == 1
    assert profile["distinct_icon_cell_count"] == 2
    assert profile["plr_weight_sum"]["min"] == pytest.approx(1.0)
    assert (
        profile["plr_area_coverage_ratio"]["min"]
        == pytest.approx(1.0)
    )


def test_profile_bridge_reports_duplicate_pair():
    bridge = pd.DataFrame(
        {
            "plr_id": ["A", "A"],
            "cell_index": [10, 10],
            "intersection_area_m2": [50.0, 50.0],
            "plr_area_m2": [100.0, 100.0],
            "icon_cell_area_m2": [100.0, 100.0],
            "fraction_of_plr": [0.5, 0.5],
            "fraction_of_icon_cell": [0.5, 0.5],
        }
    )

    profile = profile_icon_plr_bridge(
        bridge
    )

    assert (
        profile["duplicate_plr_cell_pair_count"]
        == 1
    )


def test_profile_bridge_reports_referential_gaps():
    bridge = pd.DataFrame(
        {
            "plr_id": ["A", "B"],
            "cell_index": [10, 99],
            "intersection_area_m2": [50.0, 50.0],
            "plr_area_m2": [50.0, 50.0],
            "icon_cell_area_m2": [100.0, 100.0],
            "fraction_of_plr": [1.0, 1.0],
            "fraction_of_icon_cell": [0.5, 0.5],
        }
    )

    lor_ids = pd.Series(
        ["A", "B", "C"]
    )
    icon_ids = pd.Series(
        [10, 11]
    )

    profile = profile_icon_plr_bridge(
        bridge,
        lor_ids=lor_ids,
        icon_cell_ids=icon_ids,
    )

    lor = profile["lor_referential_coverage"]
    icon = profile["icon_referential_coverage"]

    assert lor["missing_plr_count"] == 1
    assert lor["missing_plr_ids"] == ["C"]
    assert (
        icon["bridge_cells_missing_from_source_count"]
        == 1
    )
    assert (
        icon["bridge_cells_missing_from_source"]
        == [99]
    )


# ---------------------------------------------------------------------------
# Unit test: tiny synthetic spatial intersection
# ---------------------------------------------------------------------------

def test_synthetic_spatial_intersection_produces_known_weights():
    """
    One 100 m² square PLR is split by two rectangles into 25% and 75%.

    This verifies the spatial weighting idea independently of Berlin data.
    """
    plr = gpd.GeoDataFrame(
        {"plr_id": ["A"]},
        geometry=[
            Polygon(
                [
                    (0, 0),
                    (10, 0),
                    (10, 10),
                    (0, 10),
                ]
            )
        ],
        crs="EPSG:25833",
    )

    cells = gpd.GeoDataFrame(
        {"cell_index": [10, 11]},
        geometry=[
            Polygon(
                [
                    (0, 0),
                    (2.5, 0),
                    (2.5, 10),
                    (0, 10),
                ]
            ),
            Polygon(
                [
                    (2.5, 0),
                    (10, 0),
                    (10, 10),
                    (2.5, 10),
                ]
            ),
        ],
        crs="EPSG:25833",
    )

    intersections = gpd.overlay(
        plr,
        cells,
        how="intersection",
        keep_geom_type=True,
    )

    intersections["intersection_area_m2"] = (
        intersections.geometry.area
    )
    intersections["plr_area_m2"] = 100.0
    intersections["fraction_of_plr"] = (
        intersections["intersection_area_m2"]
        / intersections["plr_area_m2"]
    )

    weights = (
        intersections
        .sort_values("cell_index")
        ["fraction_of_plr"]
        .to_numpy()
    )

    np.testing.assert_allclose(
        weights,
        [0.25, 0.75],
        atol=1e-12,
    )
    assert weights.sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Real-data contract tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_bridge() -> pd.DataFrame:
    if not BRIDGE_FILE.exists():
        pytest.skip(
            f"Bridge fixture not available: {BRIDGE_FILE}"
        )

    return pd.read_parquet(
        BRIDGE_FILE
    )


@pytest.fixture(scope="module")
def real_lor() -> gpd.GeoDataFrame:
    if not LOR_FILE.exists():
        pytest.skip(
            f"LOR fixture not available: {LOR_FILE}"
        )

    return gpd.read_file(
        LOR_FILE
    )


@pytest.fixture(scope="module")
def real_icon_cells() -> pd.DataFrame:
    if not ICON_CELLS_FILE.exists():
        pytest.skip(
            f"ICON cells fixture not available: {ICON_CELLS_FILE}"
        )

    return pd.read_parquet(
        ICON_CELLS_FILE,
        columns=["cell_index"],
    )


def test_real_bridge_has_expected_columns(
    real_bridge: pd.DataFrame,
):
    required = {
        "plr_id",
        "cell_index",
        "intersection_area_m2",
        "plr_area_m2",
        "icon_cell_area_m2",
        "fraction_of_plr",
        "fraction_of_icon_cell",
    }

    assert required <= set(
        real_bridge.columns
    )


def test_real_bridge_has_no_duplicate_plr_cell_pairs(
    real_bridge: pd.DataFrame,
):
    assert not real_bridge.duplicated(
        subset=["plr_id", "cell_index"]
    ).any()


def test_real_bridge_has_positive_intersection_areas(
    real_bridge: pd.DataFrame,
):
    assert (
        real_bridge["intersection_area_m2"] > 0
    ).all()


def test_real_bridge_fractions_are_within_bounds(
    real_bridge: pd.DataFrame,
):
    tolerance = 1e-9

    assert (
        real_bridge["fraction_of_plr"] > 0
    ).all()
    assert (
        real_bridge["fraction_of_plr"] <= 1 + tolerance
    ).all()

    assert (
        real_bridge["fraction_of_icon_cell"] > 0
    ).all()
    assert (
        real_bridge["fraction_of_icon_cell"] <= 1 + tolerance
    ).all()


def test_real_bridge_contains_all_lor_plrs(
    real_bridge: pd.DataFrame,
    real_lor: gpd.GeoDataFrame,
):
    id_column = find_plr_id_column(
        list(real_lor.columns)
    )

    lor_ids = set(
        real_lor[id_column]
        .dropna()
        .astype(str)
    )
    bridge_ids = set(
        real_bridge["plr_id"]
        .dropna()
        .astype(str)
    )

    assert bridge_ids == lor_ids
    assert len(bridge_ids) == 542


def test_real_bridge_references_only_known_icon_cells(
    real_bridge: pd.DataFrame,
    real_icon_cells: pd.DataFrame,
):
    known_cells = set(
        real_icon_cells["cell_index"]
        .dropna()
        .astype("int64")
    )
    bridge_cells = set(
        real_bridge["cell_index"]
        .dropna()
        .astype("int64")
    )

    unknown = (
        bridge_cells - known_cells
    )

    assert not unknown, (
        f"{len(unknown)} bridge cell indexes are "
        "missing from the ICON grid"
    )


def test_real_bridge_plr_weights_sum_to_one(
    real_bridge: pd.DataFrame,
):
    weight_sum = (
        real_bridge.groupby("plr_id")[
            "fraction_of_plr"
        ]
        .sum()
        .to_numpy(dtype="float64")
    )

    np.testing.assert_allclose(
        weight_sum,
        1.0,
        atol=1e-6,
        rtol=0.0,
    )


def test_real_bridge_intersection_area_covers_each_plr(
    real_bridge: pd.DataFrame,
):
    coverage = (
        real_bridge.groupby("plr_id")
        .agg(
            intersection_area=(
                "intersection_area_m2",
                "sum",
            ),
            plr_area=(
                "plr_area_m2",
                "first",
            ),
        )
    )

    coverage_ratio = (
        coverage["intersection_area"]
        / coverage["plr_area"]
    ).to_numpy(dtype="float64")

    np.testing.assert_allclose(
        coverage_ratio,
        1.0,
        atol=1e-6,
        rtol=0.0,
    )
