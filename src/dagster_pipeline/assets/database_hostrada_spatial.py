"""Unpartitioned reusable spatial foundation for monthly HOSTRADA data."""

import dagster as dg

from src.database.connection import database_connection
from src.database.hostrada_state import ensure_hostrada_grid
from src.database.spatial_state import current_geography_version
from src.dagster_pipeline.assets.database_spatial import NORMALIZED_PLR_KEY


NORMALIZED_HOSTRADA_CELL_KEY = dg.AssetKey(
    ["normalized", "hostrada_cell"]
)
NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY = dg.AssetKey(
    ["normalized", "hostrada_plr_area_bridge"]
)


@dg.asset(
    key=NORMALIZED_HOSTRADA_CELL_KEY,
    deps=[NORMALIZED_PLR_KEY],
    group_name="hostrada_spatial",
    description=(
        "Berlin-intersecting HOSTRADA 1 km cells constructed from the "
        "validated EPSG:3034 grid and stored in EPSG:25833."
    ),
)
def normalized_hostrada_cell(
    context: dg.AssetExecutionContext,
) -> None:
    with database_connection(
        application_name="capstone_hostrada_cell"
    ) as connection:
        geography_version = current_geography_version(connection)
        source_grid_id = ensure_hostrada_grid(connection)
        result = connection.execute(
            """
            SELECT
                cells.cell_row_count,
                cells.represented_plr_count,
                cells.candidate_cell_count
            FROM normalized.refresh_hostrada_cell_geometry(
                %s::TEXT,
                %s::TEXT
            ) AS cells
            """,
            (geography_version, source_grid_id),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA cell transformation returned no summary")

    metadata = {
        "geography_version": geography_version,
        "source_grid_id": source_grid_id,
        "cell_row_count": int(result[0]),
        "represented_plr_count": int(result[1]),
        "candidate_cell_count": int(result[2]),
        "source_srid": 3034,
        "target_srid": 25833,
    }
    context.log.info("HOSTRADA cell materialization: %s", metadata)
    context.add_output_metadata(metadata)


@dg.asset(
    key=NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY,
    deps=[NORMALIZED_PLR_KEY, NORMALIZED_HOSTRADA_CELL_KEY],
    group_name="hostrada_spatial",
    description=(
        "PostGIS area-intersection bridge between current Berlin PLRs "
        "and the Berlin-intersecting HOSTRADA grid cells."
    ),
)
def normalized_hostrada_plr_area_bridge(
    context: dg.AssetExecutionContext,
) -> None:
    with database_connection(
        application_name="capstone_hostrada_plr_bridge"
    ) as connection:
        geography_version = current_geography_version(connection)
        source_grid_id = ensure_hostrada_grid(connection)
        result = connection.execute(
            """
            SELECT
                bridge.bridge_row_count,
                bridge.represented_plr_count,
                bridge.represented_hostrada_cell_count
            FROM normalized.refresh_hostrada_plr_area_bridge(
                %s::TEXT,
                %s::TEXT
            ) AS bridge
            """,
            (geography_version, source_grid_id),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA PLR bridge returned no summary")

    metadata = {
        "geography_version": geography_version,
        "source_grid_id": source_grid_id,
        "bridge_row_count": int(result[0]),
        "represented_plr_count": int(result[1]),
        "represented_hostrada_cell_count": int(result[2]),
    }
    context.log.info("HOSTRADA PLR bridge materialization: %s", metadata)
    context.add_output_metadata(metadata)


HOSTRADA_SPATIAL_ASSETS = [
    normalized_hostrada_cell,
    normalized_hostrada_plr_area_bridge,
]
