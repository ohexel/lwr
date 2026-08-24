"""One compact cross-table gate for each local-calendar reference month."""

import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.dagster_pipeline.assets.database_hostrada_reference import (
    ANALYTICAL_HOSTRADA_BERLIN_REFERENCE_KEY,
)
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT
from src.hostrada_reference import hostrada_reference_month_from_partition


@dg.asset_check(
    asset=ANALYTICAL_HOSTRADA_BERLIN_REFERENCE_KEY,
    name="hostrada_reference_month_complete",
)
def hostrada_reference_month_complete(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    calendar_month = hostrada_reference_month_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_hostrada_reference_check"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT *
            FROM analytical.check_hostrada_reference_month_quality(
                %s::INTEGER,
                %s::TEXT,
                %s::TEXT
            )
            """,
            (
                calendar_month,
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA reference quality check returned no result")

    metadata = {
        "calendar_month": context.partition_key,
        "expected_plr_count": int(result[1]),
        "expected_calendar_hour_count": int(result[2]),
        "expected_observation_count": int(result[3]),
        "source_month_count": int(result[4]),
        "source_month_failure_count": int(result[5]),
        "plr_reference_count": int(result[6]),
        "berlin_reference_count": int(result[7]),
        "plr_sample_count_failure_count": int(result[8]),
        "berlin_sample_count_failure_count": int(result[9]),
    }
    context.log.info("HOSTRADA reference quality result: %s", metadata)

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
        description=(
            "All PLRs and Berlin calendar hours exist, source manifests are "
            "complete, and sample counts match the timezone-aware UTC history."
        ),
    )


HOSTRADA_REFERENCE_CHECKS = [
    hostrada_reference_month_complete,
]
