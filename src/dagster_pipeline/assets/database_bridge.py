import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import (
    current_geography_version,
)
from src.dagster_pipeline.assets.database_spatial import (
    NORMALIZED_ICON_CELL_KEY,
    NORMALIZED_PLR_KEY,
)
from src.icon_grid_contract import (
    ICON_D2_GRID_CONTRACT,
)


NORMALIZED_ICON_PLR_BRIDGE_KEY = dg.AssetKey(
    ["normalized", "icon_plr_area_bridge"]
)



@dg.asset(
    key=NORMALIZED_ICON_PLR_BRIDGE_KEY,
    deps=[
        NORMALIZED_PLR_KEY,
        NORMALIZED_ICON_CELL_KEY,
    ],
    group_name="normalized",
    description=(
        "PostGIS area-intersection bridge between the "
        "current Berlin PLR geography and the ICON D2 grid."
    ),
)
def normalized_icon_plr_area_bridge(
    context: dg.AssetExecutionContext,
) -> None:
    with database_connection(
        application_name="capstone_icon_plr_bridge"
    ) as connection:
        geography_version = current_geography_version(
            connection
        )

        result = connection.execute(
            """
            SELECT
                bridge.bridge_row_count,
                bridge.represented_plr_count,
                bridge.represented_icon_cell_count
            FROM normalized.refresh_icon_plr_area_bridge(
                %s::TEXT,
                %s::TEXT
            ) AS bridge
            """,
            (
                geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError(
            "PostGIS bridge transformation returned no summary"
        )

    context.add_output_metadata(
        {
            "geography_version": geography_version,
            "source_grid_id": ICON_D2_GRID_CONTRACT.source_grid_id,
            "bridge_row_count": int(result[0]),
            "represented_plr_count": int(result[1]),
            "represented_icon_cell_count": int(result[2]),
        }
    )


BRIDGE_ASSETS = [
    normalized_icon_plr_area_bridge,
]
