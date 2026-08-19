from __future__ import annotations

import argparse
import bz2
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset


DEFAULT_INPUT = Path(
    "data/raw/icon-d2-grid/icon_grid_0047_R19B07_L.nc.bz2"
)

DEFAULT_OUTPUT = Path(
    "reports/profiling/icon_grid_profile.json"
)


REQUIRED_VARIABLES = (
    "clon",
    "clat",
    "vlon",
    "vlat",
    "vertex_of_cell",
)


def radians_to_degrees(values: np.ndarray) -> np.ndarray:
    """Convert radians to degrees."""
    return np.degrees(np.asarray(values, dtype="float64"))


def normalize_vertex_of_cell(
    vertex_of_cell: np.ndarray,
    *,
    vertex_count: int,
) -> np.ndarray:
    """
    Normalize ICON connectivity to shape (n_cells, 3) and zero-based indexes.

    ICON grid files commonly store vertex_of_cell as (3, n_cells) using
    one-based indexing. This function accepts either orientation and converts
    the array into the representation expected by our Python code.
    """
    connectivity = np.asarray(vertex_of_cell)

    if connectivity.ndim != 2:
        raise ValueError(
            "vertex_of_cell must be a 2D array; "
            f"got shape {connectivity.shape}"
        )

    if connectivity.shape[0] == 3:
        connectivity = connectivity.T
    elif connectivity.shape[1] == 3:
        connectivity = connectivity.copy()
    else:
        raise ValueError(
            "vertex_of_cell must have one dimension of size 3; "
            f"got shape {connectivity.shape}"
        )

    if not np.issubdtype(connectivity.dtype, np.integer):
        connectivity = connectivity.astype("int64")

    min_index = int(connectivity.min())
    max_index = int(connectivity.max())

    # Detect one-based connectivity, which is the expected ICON convention.
    if min_index >= 1 and max_index <= vertex_count:
        connectivity = connectivity - 1

    min_index = int(connectivity.min())
    max_index = int(connectivity.max())

    if min_index < 0 or max_index >= vertex_count:
        raise ValueError(
            "vertex_of_cell contains indexes outside the available "
            f"vertex range 0..{vertex_count - 1}: "
            f"observed {min_index}..{max_index}"
        )

    return connectivity.astype("int64", copy=False)


def decompress_bz2_to_tempfile(path: Path) -> Path:
    """
    Decompress a .bz2 file into a temporary NetCDF file.

    Caller is responsible for deleting the returned file.
    """
    temp = tempfile.NamedTemporaryFile(
        suffix=".nc",
        delete=False,
    )
    temp_path = Path(temp.name)
    temp.close()

    with bz2.open(path, "rb") as src, temp_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)

    return temp_path


def profile_icon_grid(nc_path: Path) -> dict[str, Any]:
    """
    Profile the static ICON-D2 grid definition.

    This records structural facts and basic coordinate/topology observations.
    It does not reconstruct all polygons; that belongs to downstream spatial
    processing and its own tests.
    """
    with Dataset(nc_path) as ds:
        missing_variables = [
            name
            for name in REQUIRED_VARIABLES
            if name not in ds.variables
        ]

        if missing_variables:
            raise ValueError(
                "ICON grid is missing required variables: "
                f"{missing_variables}"
            )

        clon = np.asarray(ds.variables["clon"][:])
        clat = np.asarray(ds.variables["clat"][:])
        vlon = np.asarray(ds.variables["vlon"][:])
        vlat = np.asarray(ds.variables["vlat"][:])
        raw_vertex_of_cell = np.asarray(
            ds.variables["vertex_of_cell"][:]
        )

        cell_count = int(clon.size)
        vertex_count = int(vlon.size)

        connectivity = normalize_vertex_of_cell(
            raw_vertex_of_cell,
            vertex_count=vertex_count,
        )

        if clat.size != cell_count:
            raise ValueError(
                "clon and clat lengths do not match: "
                f"{clon.size} vs {clat.size}"
            )

        if vlat.size != vertex_count:
            raise ValueError(
                "vlon and vlat lengths do not match: "
                f"{vlon.size} vs {vlat.size}"
            )

        if connectivity.shape[0] != cell_count:
            raise ValueError(
                "Connectivity cell count does not match coordinate "
                f"cell count: {connectivity.shape[0]} vs {cell_count}"
            )

        clon_deg = radians_to_degrees(clon)
        clat_deg = radians_to_degrees(clat)
        vlon_deg = radians_to_degrees(vlon)
        vlat_deg = radians_to_degrees(vlat)

        dimensions = {
            name: int(len(dimension))
            for name, dimension in ds.dimensions.items()
        }

        variables = {
            name: {
                "dtype": str(variable.dtype),
                "shape": list(variable.shape),
                "dimensions": list(variable.dimensions),
            }
            for name, variable in ds.variables.items()
        }

    profile: dict[str, Any] = {
        "dataset": "dwd_icon_d2_grid_0047_R19B07_L",
        "cell_count": cell_count,
        "vertex_count": vertex_count,
        "dimensions": dimensions,
        "required_variables_present": list(REQUIRED_VARIABLES),
        "variables": variables,
        "connectivity": {
            "normalized_shape": list(connectivity.shape),
            "vertices_per_cell": int(connectivity.shape[1]),
            "unique_vertices_referenced": int(
                np.unique(connectivity).size
            ),
        },
    }

    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the static DWD ICON-D2 grid definition and "
            "persist results as JSON."
        )
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Compressed ICON grid NetCDF. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Profile JSON output. Default: {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args()

    if not args.input_file.exists():
        raise FileNotFoundError(
            f"ICON grid file not found: {args.input_file}"
        )

    temp_path: Path | None = None

    try:
        if args.input_file.suffix == ".bz2":
            print(f"Decompressing: {args.input_file}")
            temp_path = decompress_bz2_to_tempfile(
                args.input_file
            )
            nc_path = temp_path
        else:
            nc_path = args.input_file

        print(f"Profiling ICON grid: {args.input_file}")
        profile = profile_icon_grid(nc_path)

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

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    print()
    print("ICON grid profile")
    print("-----------------")
    print(f"Cells:               {profile['cell_count']:,}")
    print(f"Vertices:            {profile['vertex_count']:,}")
    print(
        f"Connectivity shape:  "
        f"{profile['connectivity']['normalized_shape']}"
    )
    print(f"Profile written to:  {args.output_file}")


if __name__ == "__main__":
    main()
