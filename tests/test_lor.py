from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from src.profile_lor import find_plr_id_column, profile_lor


LOR_FILE = Path("data/raw/berlin/lor/lor_planungsraum.geojson")


# ---------------------------------------------------------------------------
# Unit tests: profiling/transformation logic
# ---------------------------------------------------------------------------

def test_find_plr_id_column_prefers_known_identifier():
    gdf = gpd.GeoDataFrame(
        {
            "plr_id": ["A"],
            "RAUMID": ["B"],
        },
        geometry=[Point(13.4, 52.5)],
        crs="EPSG:4326",
    )

    assert find_plr_id_column(gdf) == "plr_id"


def test_find_plr_id_column_raises_when_missing():
    gdf = gpd.GeoDataFrame(
        {"name": ["example"]},
        geometry=[Point(13.4, 52.5)],
        crs="EPSG:4326",
    )

    with pytest.raises(
        ValueError,
        match="Could not identify PLR ID column",
    ):
        find_plr_id_column(gdf)


def test_profile_lor_detects_duplicate_ids():
    polygon_a = Polygon(
        [(0, 0), (1, 0), (1, 1), (0, 1)]
    )
    polygon_b = Polygon(
        [(1, 0), (2, 0), (2, 1), (1, 1)]
    )

    gdf = gpd.GeoDataFrame(
        {"plr_id": ["A", "A"]},
        geometry=[polygon_a, polygon_b],
        crs="EPSG:25833",
    )

    profile = profile_lor(gdf)

    assert profile["input_feature_count"] == 2
    assert profile["identity"]["distinct_plr_id_count"] == 1
    assert profile["identity"]["duplicate_plr_id_count"] == 1


def test_profile_lor_records_crs_and_valid_geometry():
    polygon = Polygon(
        [(0, 0), (100, 0), (100, 100), (0, 100)]
    )

    gdf = gpd.GeoDataFrame(
        {"plr_id": ["A"]},
        geometry=[polygon],
        crs="EPSG:25833",
    )

    profile = profile_lor(gdf)

    assert profile["geometry"]["crs"] == "EPSG:25833"
    assert profile["geometry"]["invalid_geometry_count"] == 0
    assert profile["geometry"]["null_geometry_count"] == 0


def test_profile_lor_records_null_geometry():
    geometry = gpd.GeoSeries(
        [Point(0, 0), None],
        crs="EPSG:25833",
    )

    gdf = gpd.GeoDataFrame(
        {"plr_id": ["A", "B"]},
        geometry=geometry,
    )

    profile = profile_lor(gdf)

    assert profile["geometry"]["null_geometry_count"] == 1


# ---------------------------------------------------------------------------
# Real-data contract tests: current Architecture 1 LOR snapshot
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_plr() -> gpd.GeoDataFrame:
    if not LOR_FILE.exists():
        pytest.skip(
            f"Real LOR fixture not available: {LOR_FILE}"
        )

    return gpd.read_file(LOR_FILE)


def test_real_lor_expected_feature_count(
    real_plr: gpd.GeoDataFrame,
):
    # Version-specific expectation for the current PLR snapshot.
    assert len(real_plr) == 542


def test_real_lor_has_unique_non_null_plr_ids(
    real_plr: gpd.GeoDataFrame,
):
    id_column = find_plr_id_column(real_plr)

    assert real_plr[id_column].notna().all()
    assert real_plr[id_column].is_unique


def test_real_lor_has_expected_crs(
    real_plr: gpd.GeoDataFrame,
):
    assert real_plr.crs is not None
    assert real_plr.crs.to_epsg() == 25833


def test_real_lor_geometry_is_present_and_valid(
    real_plr: gpd.GeoDataFrame,
):
    assert real_plr.geometry.notna().all()
    assert (~real_plr.geometry.is_empty).all()
    assert real_plr.geometry.is_valid.all()


def test_real_lor_geometry_is_polygonal(
    real_plr: gpd.GeoDataFrame,
):
    allowed = {"Polygon", "MultiPolygon"}
    actual = set(real_plr.geometry.geom_type.unique())

    assert actual <= allowed
