import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_spatial import (
    NORMALIZED_ICON_CELL_KEY,
    NORMALIZED_PLR_KEY,
    RAW_ICON_GRID_KEY,
    RAW_LOR_KEY,
)
from src.ingestion.icon_grid import (
    ICON_GRID_ID,
)


@dg.asset_check(
    asset=NORMALIZED_PLR_KEY,
    additional_deps=[RAW_LOR_KEY],
    name="plr_geometry_quality",
)
def plr_geometry_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    with database_connection(
        application_name="capstone_plr_geometry_check"
    ) as connection:
        source = connection.execute(
            """
            SELECT
                raw_lor.source_sha256,
                MAX(raw_lor.loaded_at_utc)
            FROM raw.lor_plr AS raw_lor
            GROUP BY raw_lor.source_sha256
            ORDER BY MAX(raw_lor.loaded_at_utc) DESC
            LIMIT 1
            """
        ).fetchone()

        if source is None:
            return dg.AssetCheckResult(
                passed=False,
                description=(
                    "No raw LOR source is available "
                    "for validation."
                ),
            )

        source_sha256 = str(
            source[0]
        )

        result = connection.execute(
            """
            SELECT
                quality.passed,
                quality.source_row_count,
                quality.normalized_row_count,
                quality.rejected_row_count,
                quality.invalid_normalized_geometry_count,
                quality.wrong_srid_count,
                quality.geography_version,
                quality.rejection_reasons
            FROM normalized.check_plr_geometry_quality(%s)
                AS quality
            """,
            (source_sha256,),
        ).fetchone()

    if result is None:
        return dg.AssetCheckResult(
            passed=False,
            description=(
                "PLR geometry SQL quality function "
                "returned no result."
            ),
        )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata={
            "source_sha256": source_sha256,
            "source_row_count": int(
                result[1]
            ),
            "normalized_row_count": int(
                result[2]
            ),
            "rejected_row_count": int(
                result[3]
            ),
            "invalid_geometry_count": int(
                result[4]
            ),
            "wrong_srid_count": int(
                result[5]
            ),
            "geography_version": str(
                result[6]
            ),
            "rejection_reasons": result[7],
        },
        description=(
            "PostgreSQL validates current PLR count, "
            "geometry validity, quarantine state, "
            "and EPSG:25833."
        ),
    )


@dg.asset_check(
    asset=NORMALIZED_ICON_CELL_KEY,
    additional_deps=[RAW_ICON_GRID_KEY],
    name="icon_cell_geometry_quality",
)
def icon_cell_geometry_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    with database_connection(
        application_name="capstone_icon_geometry_check"
    ) as connection:
        result = connection.execute(
            """
            SELECT
                quality.passed,
                quality.raw_vertex_count,
                quality.raw_cell_count,
                quality.topology_row_count,
                quality.normalized_cell_count,
                quality.rejected_cell_count,
                quality.invalid_normalized_geometry_count,
                quality.wrong_srid_count,
                quality.non_triangle_count,
                quality.rejection_reasons
            FROM normalized.check_icon_geometry_quality(%s)
                AS quality
            """,
            (ICON_GRID_ID,),
        ).fetchone()

    if result is None:
        return dg.AssetCheckResult(
            passed=False,
            description=(
                "ICON geometry SQL quality function "
                "returned no result."
            ),
        )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata={
            "source_grid_id": ICON_GRID_ID,
            "raw_vertex_count": int(
                result[1]
            ),
            "raw_cell_count": int(
                result[2]
            ),
            "topology_row_count": int(
                result[3]
            ),
            "normalized_cell_count": int(
                result[4]
            ),
            "rejected_cell_count": int(
                result[5]
            ),
            "invalid_geometry_count": int(
                result[6]
            ),
            "wrong_srid_count": int(
                result[7]
            ),
            "non_triangle_count": int(
                result[8]
            ),
            "rejection_reasons": result[9],
        },
        description=(
            "PostgreSQL validates ICON grid cardinality, "
            "three-vertex topology, geometry validity, "
            "quarantine state, and EPSG:25833."
        ),
    )


ARCHITECTURE_3_SPATIAL_CHECKS = [
    plr_geometry_quality,
    icon_cell_geometry_quality,
]
