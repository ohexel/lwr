"""Dagster quality checks for the reusable HOSTRADA spatial bridge."""

import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.dagster_pipeline.assets.database_hostrada_spatial import (
    NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY,
)
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT


@dg.asset_check(
    asset=NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY,
    name="hostrada_plr_area_bridge_quality",
)
def hostrada_plr_area_bridge_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    with database_connection(
        application_name="capstone_hostrada_plr_bridge_check"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT *
            FROM normalized.check_hostrada_plr_area_bridge_quality(
                %s::TEXT,
                %s::TEXT
            )
            """,
            (
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA bridge quality check returned no result")

    metadata = {
        "geography_version": geography_version,
        "source_grid_id": HOSTRADA_GRID_CONTRACT.source_grid_id,
        "bridge_row_count": int(result[1]),
        "source_plr_count": int(result[2]),
        "represented_plr_count": int(result[3]),
        "source_hostrada_cell_count": int(result[4]),
        "represented_hostrada_cell_count": int(result[5]),
        "missing_plr_count": int(result[6]),
        "unused_hostrada_cell_count": int(result[7]),
        "orphan_plr_count": int(result[8]),
        "orphan_hostrada_cell_count": int(result[9]),
        "nonpositive_area_count": int(result[10]),
        "invalid_fraction_count": int(result[11]),
        "plr_weight_failure_count": int(result[12]),
        "max_plr_weight_error": float(result[13]),
    }
    context.log.info("HOSTRADA PLR bridge quality result: %s", metadata)

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
        description=(
            "PostgreSQL validates complete Berlin PLR and HOSTRADA-cell "
            "coverage, positive overlap areas, valid fractions, and "
            "PLR area-weight conservation."
        ),
    )


HOSTRADA_SPATIAL_CHECKS = [
    hostrada_plr_area_bridge_quality,
]
