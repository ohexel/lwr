from __future__ import annotations

import bz2
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from netCDF4 import Dataset
from shapely import polygons


GRID_FILE = Path(
    "data/raw/icon-d2-grid/icon_grid_0047_R19B07_L.nc.bz2"
)

PLR_FILE = Path(
    "data/raw/berlin/lor/lor_planungsraum.geojson"
)

OUTPUT_FILE = Path(
    "data/silver/icon-d2-grid/icon_plr_area_bridge.parquet"
)

ICON_BERLIN_FILE = Path(
    "data/silver/icon-d2-grid/icon_cells_intersecting_berlin.parquet"
)

TARGET_CRS = "EPSG:25833"


def find_plr_id_column(plr: gpd.GeoDataFrame) -> str:
    candidates = [
        "RAUMID",
        "raumid",
        "PLR_ID",
        "plr_id",
        "PLR",
        "plr",
    ]

    for column in candidates:
        if column in plr.columns:
            return column

    raise ValueError(
        "Could not identify PLR ID column. "
        f"Available columns: {list(plr.columns)}"
    )


def to_degrees(values: np.ndarray, units: str | None) -> np.ndarray:
    values = np.asarray(values, dtype="float64")
    units_lower = (units or "").lower()

    if "radian" in units_lower:
        return np.rad2deg(values)

    # ICON coordinates are normally radians. Keep a defensive fallback.
    if np.nanmax(np.abs(values)) <= (2 * np.pi + 0.1):
        return np.rad2deg(values)

    return values


def read_icon_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not GRID_FILE.exists():
        raise FileNotFoundError(f"Missing ICON grid file: {GRID_FILE}")

    print(f"Reading ICON grid: {GRID_FILE}")
    print("Temporarily decompressing NetCDF...")

    with tempfile.NamedTemporaryFile(
        suffix=".nc",
        delete=False,
    ) as tmp:
        nc_path = Path(tmp.name)

    try:
        with bz2.open(GRID_FILE, "rb") as source:
            with nc_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)

        with Dataset(nc_path, "r") as ds:
            required = {"vlon", "vlat", "vertex_of_cell"}
            missing = required - set(ds.variables)

            if missing:
                raise KeyError(
                    f"ICON grid missing variables: {sorted(missing)}"
                )

            vlon_var = ds.variables["vlon"]
            vlat_var = ds.variables["vlat"]

            vlon = to_degrees(
                np.asarray(vlon_var[:]).reshape(-1),
                getattr(vlon_var, "units", None),
            )
            vlat = to_degrees(
                np.asarray(vlat_var[:]).reshape(-1),
                getattr(vlat_var, "units", None),
            )

            vertex_of_cell = np.asarray(
                ds.variables["vertex_of_cell"][:]
            )

    finally:
        nc_path.unlink(missing_ok=True)

    # Normalize connectivity to shape (n_cells, 3).
    if vertex_of_cell.shape[0] == 3:
        vertex_of_cell = vertex_of_cell.T

    if vertex_of_cell.ndim != 2 or vertex_of_cell.shape[1] != 3:
        raise ValueError(
            "Unexpected vertex_of_cell shape: "
            f"{vertex_of_cell.shape}"
        )

    vertex_of_cell = vertex_of_cell.astype("int64")

    # ICON connectivity is normally 1-based.
    if vertex_of_cell.min() >= 1:
        vertex_of_cell -= 1

    if vertex_of_cell.min() < 0:
        raise ValueError(
            "Negative ICON vertex index found after index normalization."
        )

    print(f"ICON cells:    {len(vertex_of_cell):,}")
    print(f"ICON vertices: {len(vlon):,}")

    return vlon, vlat, vertex_of_cell


def build_candidate_icon_cells(
    vlon: np.ndarray,
    vlat: np.ndarray,
    vertex_of_cell: np.ndarray,
    berlin_wgs84: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Build triangles only for cells whose vertex bounding boxes overlap
    Berlin's WGS84 bounding box.

    This keeps the operation fast while still including boundary-crossing
    ICON cells. It does not rely on cell-centre inclusion.
    """
    minx, miny, maxx, maxy = berlin_wgs84.total_bounds

    cell_vlon = vlon[vertex_of_cell]
    cell_vlat = vlat[vertex_of_cell]

    cell_minx = cell_vlon.min(axis=1)
    cell_maxx = cell_vlon.max(axis=1)
    cell_miny = cell_vlat.min(axis=1)
    cell_maxy = cell_vlat.max(axis=1)

    candidate_mask = (
        (cell_maxx >= minx)
        & (cell_minx <= maxx)
        & (cell_maxy >= miny)
        & (cell_miny <= maxy)
    )

    candidate_indices = np.flatnonzero(candidate_mask)

    print(
        f"ICON bbox candidates around Berlin: "
        f"{len(candidate_indices):,}"
    )

    coords = np.stack(
        [
            cell_vlon[candidate_indices],
            cell_vlat[candidate_indices],
        ],
        axis=-1,
    )

    triangle_geometry = polygons(coords)

    icon = gpd.GeoDataFrame(
        {
            "cell_index": candidate_indices.astype("int64"),
        },
        geometry=triangle_geometry,
        crs="EPSG:4326",
    )

    return icon


def main() -> None:
    print(f"Reading PLRs: {PLR_FILE}")
    plr = gpd.read_file(PLR_FILE)

    if plr.empty:
        raise ValueError("PLR file contains no features.")

    if plr.crs is None:
        raise ValueError("PLR file has no CRS.")

    plr_id_column = find_plr_id_column(plr)

    print(f"PLRs: {len(plr):,}")
    print(f"PLR source CRS: {plr.crs}")
    print(f"PLR ID column: {plr_id_column}")

    plr = plr[
        [plr_id_column, "geometry"]
    ].rename(
        columns={plr_id_column: "plr_id"}
    )

    plr["plr_id"] = plr["plr_id"].astype(str)

    # Repair only if needed.
    if not plr.geometry.is_valid.all():
        print("Repairing invalid PLR geometries...")
        plr["geometry"] = plr.geometry.make_valid()

    plr_projected = plr.to_crs(TARGET_CRS)
    plr_wgs84 = plr.to_crs("EPSG:4326")

    # Area must be calculated in a projected CRS.
    plr_projected["plr_area_m2"] = (
        plr_projected.geometry.area
    )

    vlon, vlat, vertex_of_cell = read_icon_grid()

    icon_candidates = build_candidate_icon_cells(
        vlon,
        vlat,
        vertex_of_cell,
        plr_wgs84,
    )

    # Project triangles before calculating areas/intersections.
    icon_projected = icon_candidates.to_crs(TARGET_CRS)

    if not icon_projected.geometry.is_valid.all():
        print("Repairing invalid ICON triangle geometries...")
        icon_projected["geometry"] = (
            icon_projected.geometry.make_valid()
        )

    icon_projected["icon_cell_area_m2"] = (
        icon_projected.geometry.area
    )

    print("Finding actual ICON cells intersecting Berlin...")

    berlin_union = plr_projected.geometry.union_all()

    intersects_mask = icon_projected.geometry.intersects(
        berlin_union
    )

    icon_berlin = icon_projected[
        intersects_mask
    ].copy()

    print(
        f"ICON cells intersecting Berlin: "
        f"{len(icon_berlin):,}"
    )

    # Persist the actual set of ICON cells relevant to Berlin.
    ICON_BERLIN_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    icon_berlin[
        ["cell_index", "icon_cell_area_m2"]
    ].to_parquet(
        ICON_BERLIN_FILE,
        index=False,
    )

    print("Computing PLR × ICON triangle intersections...")

    # Overlay returns one row for every non-empty polygon intersection.
    intersections = gpd.overlay(
        plr_projected,
        icon_berlin,
        how="intersection",
        keep_geom_type=True,
    )

    if intersections.empty:
        raise RuntimeError(
            "No PLR/ICON intersections were produced."
        )

    intersections["intersection_area_m2"] = (
        intersections.geometry.area
    )

    # Remove zero-area boundary touches. We need area overlaps.
    intersections = intersections[
        intersections["intersection_area_m2"] > 0
    ].copy()

    intersections["fraction_of_plr"] = (
        intersections["intersection_area_m2"]
        / intersections["plr_area_m2"]
    )

    intersections["fraction_of_icon_cell"] = (
        intersections["intersection_area_m2"]
        / intersections["icon_cell_area_m2"]
    )

    bridge = intersections[
        [
            "plr_id",
            "cell_index",
            "intersection_area_m2",
            "plr_area_m2",
            "icon_cell_area_m2",
            "fraction_of_plr",
            "fraction_of_icon_cell",
        ]
    ].copy()

    bridge = bridge.sort_values(
        ["plr_id", "cell_index"]
    ).reset_index(drop=True)

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    bridge.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    # ------------------------------------------------------------
    # Validation / useful presentation stats
    # ------------------------------------------------------------

    plr_coverage = (
        bridge.groupby("plr_id")["fraction_of_plr"]
        .sum()
        .rename("coverage")
    )

    overlaps_per_plr = (
        bridge.groupby("plr_id")
        .size()
        .rename("icon_cells")
    )

    print()
    print("Area-weighted ICON ↔ PLR bridge complete")
    print("----------------------------------------")
    print(f"PLRs:                       {len(plr):,}")
    print(
        f"ICON cells intersect Berlin: "
        f"{bridge['cell_index'].nunique():,}"
    )
    print(f"Intersection rows:           {len(bridge):,}")
    print(
        f"Mean ICON cells per PLR:     "
        f"{overlaps_per_plr.mean():.2f}"
    )
    print(
        f"Median ICON cells per PLR:   "
        f"{overlaps_per_plr.median():.0f}"
    )
    print(
        f"Max ICON cells per PLR:      "
        f"{overlaps_per_plr.max():,}"
    )
    print()
    print("PLR area coverage by ICON triangles")
    print("-----------------------------------")
    print(
        f"Minimum: {plr_coverage.min():.6f}"
    )
    print(
        f"Mean:    {plr_coverage.mean():.6f}"
    )
    print(
        f"Maximum: {plr_coverage.max():.6f}"
    )
    print()
    print(f"Bridge:       {OUTPUT_FILE}")
    print(f"Berlin cells: {ICON_BERLIN_FILE}")

    # A strong grid should cover essentially all PLR area.
    low_coverage = plr_coverage[
        plr_coverage < 0.999
    ]

    if not low_coverage.empty:
        print()
        print(
            "WARNING: Some PLRs have <99.9% ICON coverage:"
        )
        print(low_coverage.sort_values().head(10))


if __name__ == "__main__":
    main()
