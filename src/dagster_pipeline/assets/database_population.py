from pathlib import Path

import dagster as dg

from src.database.connection import database_connection
from src.ingestion.afs_population import (
    DEFAULT_POPULATION_PATH,
    load_afs_population_raw,
)


RAW_AFS_POPULATION_KEY = dg.AssetKey(
    ["raw", "afs_population"]
)
NORMALIZED_POPULATION_KEY = dg.AssetKey(
    ["normalized", "plr_population_65plus"]
)
REJECTED_POPULATION_KEY = dg.AssetKey(
    ["normalized", "plr_population_rejected"]
)


@dg.asset(
    key=RAW_AFS_POPULATION_KEY,
    group_name="raw",
    description=(
        "Source-faithful decoded AfS PLR population snapshot "
        "loaded into PostgreSQL."
    ),
)
def raw_afs_population(
    context: dg.AssetExecutionContext,
) -> None:
    result = load_afs_population_raw(
        DEFAULT_POPULATION_PATH
    )

    context.add_output_metadata(
        {
            "target_table": result.target_table,
            "row_count": result.row_count,
            "source_path": result.source_path,
            "source_sha256": result.source_sha256,
            "load_duration_seconds": (
                result.duration_seconds
            ),
        }
    )


@dg.multi_asset(
    outs={
        "population_accepted": dg.AssetOut(
            key=NORMALIZED_POPULATION_KEY
        ),
        "population_rejected": dg.AssetOut(
            key=REJECTED_POPULATION_KEY
        ),
    },
    deps=[RAW_AFS_POPULATION_KEY],
    group_name="normalized",
    description=(
        "SQL-owned AfS population quality gate. "
        "Accepted and rejected rows are persisted separately."
    ),
)
def normalized_afs_population_quality_gate(
    context: dg.AssetExecutionContext,
):
    with database_connection(
        application_name="capstone_population_quality_gate"
    ) as connection:
        source = connection.execute(
            """
            SELECT source_sha256
            FROM raw.afs_population
            GROUP BY source_sha256
            ORDER BY MAX(loaded_at_utc) DESC
            LIMIT 1
            """
        ).fetchone()

        if source is None:
            raise RuntimeError(
                "raw.afs_population contains no source rows"
            )

        source_sha256 = str(source[0])

        summary = connection.execute(
            """
            SELECT
                source_row_count,
                accepted_row_count,
                rejected_row_count,
                rejection_reasons,
                reference_date
            FROM normalized.refresh_plr_population(%s)
            """,
            (source_sha256,),
        ).fetchone()

        if summary is None:
            raise RuntimeError(
                "Population SQL quality gate returned no summary"
            )

    metadata = {
        "source_sha256": source_sha256,
        "source_row_count": int(summary[0]),
        "accepted_row_count": int(summary[1]),
        "rejected_row_count": int(summary[2]),
        "rejection_reasons": summary[3],
        "reference_date": str(summary[4]),
    }

    yield dg.Output(
        None,
        output_name="population_accepted",
        metadata=metadata,
    )
    yield dg.Output(
        None,
        output_name="population_rejected",
        metadata=metadata,
    )


POPULATION_ASSETS = [
    raw_afs_population,
    normalized_afs_population_quality_gate,
]
