import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import (
    current_geography_version,
)
from src.dagster_pipeline.assets.database_bridge import (
    NORMALIZED_ICON_PLR_BRIDGE_KEY,
)
from src.ingestion.icon_grid import ICON_GRID_ID


@dg.asset_check(
    asset=NORMALIZED_ICON_PLR_BRIDGE_KEY,
    name="icon_plr_area_bridge_quality",
)
def icon_plr_area_bridge_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    with database_connection(
        application_name="capstone_icon_plr_bridge_check"
    ) as connection:
        geography_version = current_geography_version(
            connection
        )

        result = connection.execute(
            """
            SELECT
                quality.passed,
                quality.bridge_row_count,
                quality.source_plr_count,
                quality.represented_plr_count,
                quality.represented_icon_cell_count,
                quality.missing_plr_count,
                quality.orphan_plr_count,
                quality.orphan_icon_cell_count,
                quality.nonpositive_area_count,
                quality.invalid_fraction_count,
                quality.plr_weight_failure_count,
                quality.max_plr_weight_error
            FROM normalized.check_icon_plr_area_bridge_quality(
                %s::TEXT,
                %s::TEXT
            ) AS quality
            """,
            (
                geography_version,
                ICON_GRID_ID,
            ),
        ).fetchone()

    if result is None:
        return dg.AssetCheckResult(
            passed=False,
            description=(
                "Bridge SQL quality function returned no result."
            ),
        )

    metadata = {
        "geography_version": geography_version,
        "source_grid_id": ICON_GRID_ID,
        "bridge_row_count": int(result[1]),
        "source_plr_count": int(result[2]),
        "represented_plr_count": int(result[3]),
        "represented_icon_cell_count": int(result[4]),
        "missing_plr_count": int(result[5]),
        "orphan_plr_count": int(result[6]),
        "orphan_icon_cell_count": int(result[7]),
        "nonpositive_area_count": int(result[8]),
        "invalid_fraction_count": int(result[9]),
        "plr_weight_failure_count": int(result[10]),
        "max_plr_weight_error": float(result[11]),
    }

    context.log.info(
        "ICON PLR bridge quality result: %s",
        metadata,
    )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
        description=(
            "PostgreSQL validates PLR coverage, referential "
            "integrity, positive overlap areas, valid fractions, "
            "and area-weight conservation for the PostGIS bridge."
        ),
    )


ARCHITECTURE_3_BRIDGE_CHECKS = [
    icon_plr_area_bridge_quality,
]
