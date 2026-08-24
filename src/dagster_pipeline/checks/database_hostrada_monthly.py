"""One concise completeness gate for both monthly HOSTRADA outputs."""

import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.dagster_pipeline.assets.database_hostrada_monthly import (
    ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY,
)
from src.dagster_pipeline.partitions import hostrada_month_from_partition
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT


@dg.asset_check(
    asset=ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY,
    name="hostrada_month_complete",
)
def hostrada_month_complete(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    month = hostrada_month_from_partition(context.partition_key)

    with database_connection(
        application_name="capstone_hostrada_monthly_check"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT *
            FROM analytical.check_hostrada_month_quality(
                %s::DATE,
                %s::TEXT,
                %s::TEXT
            )
            """,
            (
                month.start_utc.date(),
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA monthly quality check returned no result")

    metadata = {
        "source_month": month.partition_key,
        "geography_version": geography_version,
        "source_grid_id": HOSTRADA_GRID_CONTRACT.source_grid_id,
        "source_file_count": int(result[1]),
        "expected_hour_count": int(result[2]),
        "expected_plr_count": int(result[3]),
        "plr_hour_count": int(result[4]),
        "berlin_hour_count": int(result[5]),
        "incomplete_plr_hour_count": int(result[6]),
        "missing_berlin_hour_count": int(result[7]),
    }
    context.log.info("HOSTRADA monthly quality result: %s", metadata)

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
        description=(
            "Three source files, every UTC hour, every current PLR, "
            "and the area-weighted Berlin series are complete."
        ),
    )


HOSTRADA_MONTHLY_CHECKS = [
    hostrada_month_complete,
]
