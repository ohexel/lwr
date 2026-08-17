#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


DEFAULT_GRID_FILE = Path("data/silver/icon-d2-grid/cells.parquet")
DEFAULT_OUTPUT_DIR = Path("data/silver/icon-d2-grid")
DEFAULT_RAW_ROOT = Path("data/raw")


def find_plr_file(raw_root: Path) -> Path:
    candidates = sorted(
        {
            *raw_root.rglob("lor_planungsraum.geojson"),
            *raw_root.rglob("*planungsraum*.geojson"),
            *raw_root.rglob("*plr*.geojson"),
        }
    )

    if not candidates:
        raise FileNotFoundError(
            f"Could not find a Planungsraum GeoJSON below {raw_root}. "
            "Pass it explicitly with --plr-file."
        )

    # Prefer the canonical filename if present.
    for candidate in candidates:
        if candidate.name == "lor_planungsraum.geojson":
            return candidate

    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Filter native ICON-D2 cell centres to cells whose centres "
            "fall inside the Berlin LOR Planungsraum footprint."
        )
    )
    parser.add_argument(
        "--grid-file",
        type=Path,
        default=DEFAULT_GRID_FILE,
        help=f"Silver ICON cell table (default: {DEFAULT_GRID_FILE})",
    )
    parser.add_argument(
        "--plr-file",
        type=Path,
        help="Berlin Planungsraum GeoJSON. If omitted, search below data/raw.",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=DEFAULT_RAW_ROOT,
        help=f"Root used for PLR auto-discovery (default: {DEFAULT_RAW_ROOT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if not args.grid_file.exists():
        raise FileNotFoundError(f"Missing silver ICON grid: {args.grid_file}")

    plr_file = args.plr_file or find_plr_file(args.raw_root)

    print(f"Reading ICON grid centres: {args.grid_file}")
    cells = pd.read_parquet(args.grid_file)

    required_columns = {"cell_index", "longitude", "latitude"}
    missing = required_columns - set(cells.columns)
    if missing:
        raise ValueError(
            f"ICON grid file is missing required columns: {sorted(missing)}"
        )

    print(f"Reading Berlin PLR geometry: {plr_file}")
    plr = gpd.read_file(plr_file)

    if plr.empty:
        raise ValueError("Planungsraum geometry file contains no features")

    if plr.crs is None:
        raise ValueError("Planungsraum geometry has no CRS")

    print(f"PLR features: {len(plr):,}")
    print(f"PLR source CRS: {plr.crs}")

    # ICON longitude/latitude are WGS84 geographic coordinates.
    plr_wgs84 = plr.to_crs("EPSG:4326")

    # Union of all PLRs represents the Berlin LOR footprint.
    berlin_geometry = plr_wgs84.geometry.union_all()

    cell_points = gpd.GeoDataFrame(
        cells.copy(),
        geometry=gpd.points_from_xy(
            cells["longitude"],
            cells["latitude"],
        ),
        crs="EPSG:4326",
    )

    berlin_cells = cell_points[
        cell_points.geometry.within(berlin_geometry)
        | cell_points.geometry.touches(berlin_geometry)
    ].copy()

    berlin_cells = berlin_cells.sort_values("cell_index")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = args.output_dir / "cells_berlin.parquet"
    geojson_path = args.output_dir / "cells_berlin.geojson"

    # Tabular version for later joins.
    berlin_cells.drop(columns="geometry").to_parquet(
        parquet_path,
        index=False,
    )

    # Point geometry version for visual inspection / spatial joins.
    berlin_cells.to_file(
        geojson_path,
        driver="GeoJSON",
    )

    print()
    print("ICON-D2 Berlin filter complete")
    print("--------------------------------")
    print(f"All ICON cells:    {len(cells):,}")
    print(f"Berlin ICON cells: {len(berlin_cells):,}")
    print(
        f"Share of grid:     "
        f"{100 * len(berlin_cells) / len(cells):.4f}%"
    )
    print(f"Parquet:           {parquet_path}")
    print(f"GeoJSON:           {geojson_path}")
    print()
    print(
        "Method: cell centre inside Berlin PLR union. "
        "Boundary-crossing triangles whose centres fall outside Berlin are "
        "not included in this MVP filter."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
