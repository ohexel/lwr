from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import geopandas as gpd


DEFAULT_INPUT = Path("data/raw/berlin/lor/lor_planungsraum.geojson")
DEFAULT_OUTPUT = Path("reports/profiling/lor_profile.json")


def find_plr_id_column(plr: gpd.GeoDataFrame) -> str:
    """Find the Planungsraum identifier column."""
    candidates = ["plr_id", "PLR_ID", "RAUMID", "raumid", "PLR", "plr"]

    for column in candidates:
        if column in plr.columns:
            return column

    raise ValueError(
        "Could not identify PLR ID column. "
        f"Available columns: {list(plr.columns)}"
    )


def normalize_crs(crs: Any) -> str | None:
    """Return a stable human-readable CRS string."""
    if crs is None:
        return None

    try:
        epsg = crs.to_epsg()
    except Exception:
        epsg = None

    if epsg is not None:
        return f"EPSG:{epsg}"

    return str(crs)


def profile_lor(plr: gpd.GeoDataFrame) -> dict[str, Any]:
    """
    Profile the LOR Planungsraum dataset.

    Profiling records observations; it does not itself fail because of
    ordinary data-quality findings. Those findings can later become tests.
    """
    plr_id_column = find_plr_id_column(plr)

    ids = plr[plr_id_column]
    geometry = plr.geometry

    geometry_types = (
        geometry.geom_type
        .fillna("<NULL>")
        .value_counts(dropna=False)
        .sort_index()
        .to_dict()
    )

    null_geometry_count = int(geometry.isna().sum())
    empty_geometry_count = int(geometry.dropna().is_empty.sum())
    valid_mask = geometry.dropna().is_valid
    invalid_geometry_count = int((~valid_mask).sum())

    profile: dict[str, Any] = {
        "dataset": "berlin_lor_planungsraum",
        "input_feature_count": int(len(plr)),
        "schema": {
            "columns": list(plr.columns),
            "dtypes": {
                column: str(dtype)
                for column, dtype in plr.dtypes.items()
            },
        },
        "identity": {
            "plr_id_column": plr_id_column,
            "distinct_plr_id_count": int(ids.nunique(dropna=True)),
            "duplicate_plr_id_count": int(ids.duplicated().sum()),
            "null_plr_id_count": int(ids.isna().sum()),
        },
        "geometry": {
            "crs": normalize_crs(plr.crs),
            "geometry_types": geometry_types,
            "null_geometry_count": null_geometry_count,
            "empty_geometry_count": empty_geometry_count,
            "invalid_geometry_count": invalid_geometry_count,
        },
    }

    if plr.crs is not None and len(plr) > 0:
        minx, miny, maxx, maxy = plr.total_bounds
        profile["geometry"]["total_bounds"] = {
            "minx": float(minx),
            "miny": float(miny),
            "maxx": float(maxx),
            "maxy": float(maxy),
        }

        try:
            if plr.crs.is_projected:
                areas = geometry.dropna().area
                if len(areas) > 0:
                    profile["geometry"]["area_m2"] = {
                        "min": float(areas.min()),
                        "median": float(areas.median()),
                        "max": float(areas.max()),
                        "sum": float(areas.sum()),
                    }
        except Exception:
            pass

    hierarchy_candidates = {
        "bezirk": ["BEZ", "bez", "BEZIRK", "bezirk"],
        "pgr": ["PGR", "pgr"],
        "bzr": ["BZR", "bzr"],
        "plr": ["PLR", "plr"],
    }

    hierarchy: dict[str, Any] = {}

    for level, candidates in hierarchy_candidates.items():
        for column in candidates:
            if column in plr.columns:
                hierarchy[level] = {
                    "column": column,
                    "distinct_count": int(plr[column].nunique(dropna=True)),
                }
                break

    if hierarchy:
        profile["hierarchy"] = hierarchy

    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile Berlin LOR Planungsraum geometry and persist "
            "the results as JSON."
        )
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input LOR GeoJSON. Default: {DEFAULT_INPUT}",
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
            f"LOR input file not found: {args.input_file}"
        )

    print(f"Reading LOR dataset: {args.input_file}")
    plr = gpd.read_file(args.input_file)

    profile = profile_lor(plr)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print("LOR profile")
    print("-----------")
    print(f"Features:              {profile['input_feature_count']:,}")
    print(f"PLR ID column:         {profile['identity']['plr_id_column']}")
    print(
        f"Distinct PLR IDs:      "
        f"{profile['identity']['distinct_plr_id_count']:,}"
    )
    print(
        f"Duplicate PLR IDs:     "
        f"{profile['identity']['duplicate_plr_id_count']:,}"
    )
    print(
        f"Null PLR IDs:          "
        f"{profile['identity']['null_plr_id_count']:,}"
    )
    print(f"CRS:                   {profile['geometry']['crs']}")
    print(
        f"Null geometries:       "
        f"{profile['geometry']['null_geometry_count']:,}"
    )
    print(
        f"Empty geometries:      "
        f"{profile['geometry']['empty_geometry_count']:,}"
    )
    print(
        f"Invalid geometries:    "
        f"{profile['geometry']['invalid_geometry_count']:,}"
    )
    print(f"Profile written to:    {args.output_file}")


if __name__ == "__main__":
    main()
