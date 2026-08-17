from pathlib import Path

import geopandas as gpd


PLR_FILE = Path(
    "data/raw/berlin/lor/lor_planungsraum.geojson"
)

ICON_CELLS_FILE = Path(
    "data/silver/icon-d2-grid/cells_berlin.geojson"
)

OUTPUT_FILE = Path(
    "data/silver/icon-d2-grid/plr_icon_nearest.parquet"
)


def find_plr_id_column(plr: gpd.GeoDataFrame) -> str:
    """Find the LOR Planungsraum identifier column."""
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


def main() -> None:
    print(f"Reading PLRs: {PLR_FILE}")
    plr = gpd.read_file(PLR_FILE)

    print(f"Reading ICON-D2 cells: {ICON_CELLS_FILE}")
    icon = gpd.read_file(ICON_CELLS_FILE)

    plr_id_column = find_plr_id_column(plr)

    print(f"PLRs: {len(plr)}")
    print(f"ICON-D2 cells: {len(icon)}")
    print(f"PLR ID column: {plr_id_column}")

    target_crs = "EPSG:25833"

    plr = plr.to_crs(target_crs)
    icon = icon.to_crs(target_crs)

    plr_centroids = gpd.GeoDataFrame(
        {
            "plr_id": plr[plr_id_column].astype(str),
        },
        geometry=plr.geometry.centroid,
        crs=target_crs,
    )

    icon_points = icon[
        [
            "cell_index",
            "longitude",
            "latitude",
            "geometry",
        ]
    ].copy()

    mapping = gpd.sjoin_nearest(
        plr_centroids,
        icon_points,
        how="left",
        distance_col="distance_m",
    )

    mapping = mapping.drop(
        columns=["index_right"],
        errors="ignore",
    )

    output = mapping.drop(
        columns="geometry",
    ).copy()

    output = output[
        [
            "plr_id",
            "cell_index",
            "longitude",
            "latitude",
            "distance_m",
        ]
    ]

    output = output.sort_values("plr_id")

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.to_parquet(
        OUTPUT_FILE,
        index=False,
    )

    print()
    print("Nearest-cell assignment complete")
    print("--------------------------------")
    print(f"PLRs mapped:            {len(output)}")
    print(
        f"Unique ICON cells used: "
        f"{output['cell_index'].nunique()}"
    )
    print(
        f"Mean distance:          "
        f"{output['distance_m'].mean():.0f} m"
    )
    print(
        f"Median distance:        "
        f"{output['distance_m'].median():.0f} m"
    )
    print(
        f"Maximum distance:       "
        f"{output['distance_m'].max():.0f} m"
    )
    print(f"Output:                 {OUTPUT_FILE}")

    print()
    print("PLRs assigned per ICON cell")
    print("----------------------------")
    print(
        output.groupby("cell_index")
        .size()
        .describe()
    )


if __name__ == "__main__":
    main()
