import dagster as dg

from src.database.connection import database_connection
from src.ingestion.icon_grid import (
    ICON_GRID_ID,
    load_icon_grid_raw,
)
from src.ingestion.lor import (
    load_lor_raw,
)


RAW_LOR_KEY = dg.AssetKey(
    ["raw", "lor_plr"]
)

RAW_ICON_GRID_KEY = dg.AssetKey(
    ["raw", "icon_grid"]
)

NORMALIZED_PLR_KEY = dg.AssetKey(
    ["normalized", "plr"]
)

NORMALIZED_ICON_CELL_KEY = dg.AssetKey(
    ["normalized", "icon_cell"]
)


@dg.asset(
    key=RAW_LOR_KEY,
    group_name="raw",
    description=(
        "Source-faithful Berlin LOR PLR geometry "
        "decoded and loaded into PostgreSQL."
    ),
)
def raw_lor_plr(
    context: dg.AssetExecutionContext,
) -> None:
    result = load_lor_raw()

    context.add_output_metadata(
        {
            "target_table": result.target_table,
            "row_count": result.row_count,
            "source_path": result.source_path,
            "source_sha256": result.source_sha256,
            "source_crs": (
                result.source_crs
                or "unknown"
            ),
            "load_duration_seconds": (
                result.duration_seconds
            ),
        }
    )


@dg.asset(
    key=RAW_ICON_GRID_KEY,
    group_name="raw",
    description=(
        "Decoded DWD ICON D2 grid vertices and "
        "cell topology loaded into PostgreSQL."
    ),
)
def raw_icon_grid(
    context: dg.AssetExecutionContext,
) -> None:
    result = load_icon_grid_raw()

    context.add_output_metadata(
        {
            "source_grid_id": (
                result.source_grid_id
            ),
            "source_path": result.source_path,
            "source_sha256": (
                result.source_sha256
            ),
            "vertex_count": (
                result.vertex_count
            ),
            "cell_count": (
                result.cell_count
            ),
            "topology_row_count": (
                result.topology_row_count
            ),
            "vertex_load_seconds": (
                result.vertex_load_seconds
            ),
            "topology_load_seconds": (
                result.topology_load_seconds
            ),
        }
    )


@dg.asset(
    key=NORMALIZED_PLR_KEY,
    deps=[RAW_LOR_KEY],
    group_name="normalized",
    description=(
        "Canonical EPSG:25833 PLR geometry "
        "constructed and validated by PostGIS."
    ),
)
def normalized_plr(
    context: dg.AssetExecutionContext,
) -> None:
    with database_connection(
        application_name="capstone_plr_normalize"
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
            raise RuntimeError(
                "raw.lor_plr contains no source rows"
            )

        source_sha256 = str(
            source[0]
        )

        summary = connection.execute(
            """
            SELECT
                result.source_row_count,
                result.normalized_row_count,
                result.rejected_row_count,
                result.geography_version,
                result.rejection_reasons
            FROM normalized.refresh_plr_geometry(%s)
                AS result
            """,
            (source_sha256,),
        ).fetchone()

        if summary is None:
            raise RuntimeError(
                "PLR SQL normalization returned no summary"
            )

    context.add_output_metadata(
        {
            "source_sha256": source_sha256,
            "source_row_count": int(
                summary[0]
            ),
            "normalized_row_count": int(
                summary[1]
            ),
            "rejected_row_count": int(
                summary[2]
            ),
            "geography_version": str(
                summary[3]
            ),
            "rejection_reasons": (
                summary[4]
            ),
            "target_srid": 25833,
        }
    )


@dg.asset(
    key=NORMALIZED_ICON_CELL_KEY,
    deps=[RAW_ICON_GRID_KEY],
    group_name="normalized",
    description=(
        "Canonical ICON triangular cell geometry "
        "constructed and validated by PostGIS."
    ),
)
def normalized_icon_cell(
    context: dg.AssetExecutionContext,
) -> None:
    with database_connection(
        application_name="capstone_icon_cell_normalize"
    ) as connection:
        summary = connection.execute(
            """
            SELECT
                result.raw_vertex_count,
                result.raw_cell_count,
                result.normalized_cell_count,
                result.rejected_cell_count,
                result.rejection_reasons
            FROM normalized.refresh_icon_cell_geometry(%s)
                AS result
            """,
            (ICON_GRID_ID,),
        ).fetchone()

        if summary is None:
            raise RuntimeError(
                "ICON cell SQL normalization "
                "returned no summary"
            )

    context.add_output_metadata(
        {
            "source_grid_id": ICON_GRID_ID,
            "raw_vertex_count": int(
                summary[0]
            ),
            "raw_cell_count": int(
                summary[1]
            ),
            "normalized_cell_count": int(
                summary[2]
            ),
            "rejected_cell_count": int(
                summary[3]
            ),
            "rejection_reasons": (
                summary[4]
            ),
            "target_srid": 25833,
        }
    )


ARCHITECTURE_3_SPATIAL_ASSETS = [
    raw_lor_plr,
    raw_icon_grid,
    normalized_plr,
    normalized_icon_cell,
]
