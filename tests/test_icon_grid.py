from __future__ import annotations

import bz2
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from src.profile_icon_grid import (
    normalize_vertex_of_cell,
    profile_icon_grid,
    radians_to_degrees,
)


ICON_GRID_FILE = Path(
    "data/raw/icon-d2-grid/icon_grid_0047_R19B07_L.nc.bz2"
)


# ---------------------------------------------------------------------------
# Unit tests: our grid-normalization logic
# ---------------------------------------------------------------------------

def test_radians_to_degrees():
    values = np.array(
        [0.0, np.pi / 2, np.pi]
    )

    result = radians_to_degrees(values)

    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(90.0)
    assert result[2] == pytest.approx(180.0)


def test_normalize_vertex_of_cell_transposes_and_zero_bases():
    # Typical ICON-style orientation: (3, n_cells), one-based.
    raw = np.array(
        [
            [1, 2],
            [2, 4],
            [3, 3],
        ],
        dtype="int32",
    )

    normalized = normalize_vertex_of_cell(
        raw,
        vertex_count=4,
    )

    expected = np.array(
        [
            [0, 1, 2],
            [1, 3, 2],
        ]
    )

    assert normalized.shape == (2, 3)
    np.testing.assert_array_equal(
        normalized,
        expected,
    )


def test_normalize_vertex_of_cell_accepts_cell_major_zero_based():
    raw = np.array(
        [
            [0, 1, 2],
            [1, 3, 2],
        ],
        dtype="int64",
    )

    normalized = normalize_vertex_of_cell(
        raw,
        vertex_count=4,
    )

    np.testing.assert_array_equal(
        normalized,
        raw,
    )


def test_normalize_vertex_of_cell_rejects_wrong_shape():
    raw = np.zeros(
        (4, 5),
        dtype="int64",
    )

    with pytest.raises(
        ValueError,
        match="one dimension of size 3",
    ):
        normalize_vertex_of_cell(
            raw,
            vertex_count=10,
        )


def test_normalize_vertex_of_cell_rejects_out_of_range_index():
    raw = np.array(
        [
            [0, 1, 99],
        ],
        dtype="int64",
    )

    with pytest.raises(
        ValueError,
        match="outside the available vertex range",
    ):
        normalize_vertex_of_cell(
            raw,
            vertex_count=4,
        )


def test_profile_icon_grid_on_tiny_synthetic_file(
    tmp_path: Path,
):
    """
    Integration-like unit test using a tiny NetCDF file whose correct
    topology is known in advance.
    """
    nc_path = tmp_path / "tiny_grid.nc"

    with Dataset(nc_path, "w") as ds:
        ds.createDimension("cell", 2)
        ds.createDimension("vertex", 4)
        ds.createDimension("nv", 3)

        clon = ds.createVariable(
            "clon",
            "f8",
            ("cell",),
        )
        clat = ds.createVariable(
            "clat",
            "f8",
            ("cell",),
        )
        vlon = ds.createVariable(
            "vlon",
            "f8",
            ("vertex",),
        )
        vlat = ds.createVariable(
            "vlat",
            "f8",
            ("vertex",),
        )
        voc = ds.createVariable(
            "vertex_of_cell",
            "i4",
            ("nv", "cell"),
        )

        clon[:] = np.radians(
            [13.0, 13.1]
        )
        clat[:] = np.radians(
            [52.5, 52.6]
        )
        vlon[:] = np.radians(
            [13.0, 13.1, 13.2, 13.3]
        )
        vlat[:] = np.radians(
            [52.4, 52.5, 52.6, 52.7]
        )

        # Two triangular cells, one-based indexing.
        voc[:, :] = np.array(
            [
                [1, 2],
                [2, 4],
                [3, 3],
            ]
        )

    profile = profile_icon_grid(nc_path)

    assert profile["cell_count"] == 2
    assert profile["vertex_count"] == 4
    assert profile["connectivity"]["normalized_shape"] == [2, 3]
    assert profile["connectivity"]["min_vertex_index"] == 0
    assert profile["connectivity"]["max_vertex_index"] == 3


# ---------------------------------------------------------------------------
# Real-data contract tests: current ICON-D2 Grid #47 snapshot
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def decompressed_icon_grid(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    if not ICON_GRID_FILE.exists():
        pytest.skip(
            f"Real ICON grid fixture not available: {ICON_GRID_FILE}"
        )

    output_dir = tmp_path_factory.mktemp(
        "icon_grid"
    )
    nc_path = output_dir / "icon_grid.nc"

    with bz2.open(ICON_GRID_FILE, "rb") as src, nc_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)

    return nc_path


@pytest.fixture(scope="module")
def real_profile(
    decompressed_icon_grid: Path,
):
    return profile_icon_grid(
        decompressed_icon_grid
    )


def test_real_icon_grid_expected_cell_count(
    real_profile,
):
    assert real_profile["cell_count"] == 542_040


def test_real_icon_grid_expected_vertex_count(
    real_profile,
):
    assert real_profile["vertex_count"] == 272_089


def test_real_icon_grid_has_three_vertices_per_cell(
    real_profile,
):
    assert (
        real_profile["connectivity"]["vertices_per_cell"]
        == 3
    )
    assert (
        real_profile["connectivity"]["normalized_shape"]
        == [542_040, 3]
    )


def test_real_icon_grid_connectivity_is_within_vertex_range(
    real_profile,
):
    assert (
        real_profile["connectivity"]["min_vertex_index"]
        >= 0
    )
    assert (
        real_profile["connectivity"]["max_vertex_index"]
        < real_profile["vertex_count"]
    )


def test_real_icon_grid_coordinates_are_finite(
    real_profile,
):
    assert (
        real_profile["cell_centers"]["nan_longitude_count"]
        == 0
    )
    assert (
        real_profile["cell_centers"]["nan_latitude_count"]
        == 0
    )
    assert (
        real_profile["vertices"]["nan_longitude_count"]
        == 0
    )
    assert (
        real_profile["vertices"]["nan_latitude_count"]
        == 0
    )


def test_real_icon_grid_coordinates_are_geographic(
    real_profile,
):
    cell_lon = real_profile["cell_centers"]["longitude_deg"]
    cell_lat = real_profile["cell_centers"]["latitude_deg"]

    assert -180 <= cell_lon["min"] <= 180
    assert -180 <= cell_lon["max"] <= 180
    assert -90 <= cell_lat["min"] <= 90
    assert -90 <= cell_lat["max"] <= 90
