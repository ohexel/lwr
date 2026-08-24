"""Twelve restartable local-calendar HOSTRADA reference transformations."""

from time import perf_counter

import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.dagster_pipeline.assets.database_hostrada_monthly import (
    ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY,
    ANALYTICAL_HOSTRADA_PLR_HOURLY_KEY,
)
from src.dagster_pipeline.partitions import HOSTRADA_REFERENCE_PARTITIONS
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT
from src.hostrada_reference import (
    HOSTRADA_REFERENCE_END_YEAR,
    HOSTRADA_REFERENCE_START_YEAR,
    hostrada_reference_month_from_partition,
)


ANALYTICAL_HOSTRADA_PLR_REFERENCE_KEY = dg.AssetKey(
    ["analytical", "hostrada_plr_hourly_reference"]
)
ANALYTICAL_HOSTRADA_BERLIN_REFERENCE_KEY = dg.AssetKey(
    ["analytical", "hostrada_berlin_hourly_reference"]
)


@dg.multi_asset(
    outs={
        "plr_reference": dg.AssetOut(
            key=ANALYTICAL_HOSTRADA_PLR_REFERENCE_KEY
        ),
        "berlin_reference": dg.AssetOut(
            key=ANALYTICAL_HOSTRADA_BERLIN_REFERENCE_KEY
        ),
    },
    deps=[
        dg.AssetDep(
            ANALYTICAL_HOSTRADA_PLR_HOURLY_KEY,
            partition_mapping=dg.AllPartitionMapping(),
        ),
        dg.AssetDep(
            ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY,
            partition_mapping=dg.AllPartitionMapping(),
        ),
    ],
    partitions_def=HOSTRADA_REFERENCE_PARTITIONS,
    group_name="hostrada_reference",
    description=(
        "Aggregate the complete 1995-2025 HOSTRADA history into Berlin-local "
        "calendar-day/hour median, p90, and maximum references for each PLR "
        "and Berlin as a whole."
    ),
)
def analytical_hostrada_hourly_reference(
    context: dg.AssetExecutionContext,
):
    calendar_month = hostrada_reference_month_from_partition(
        context.partition_key
    )
    started = perf_counter()

    with database_connection(
        application_name="capstone_hostrada_reference_month"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT *
            FROM analytical.refresh_hostrada_reference_month(
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
        raise RuntimeError("HOSTRADA reference refresh returned no summary")

    metadata = {
        "calendar_month": context.partition_key,
        "reference_start_year": HOSTRADA_REFERENCE_START_YEAR,
        "reference_end_year": HOSTRADA_REFERENCE_END_YEAR,
        "geography_version": geography_version,
        "expected_plr_count": int(result[1]),
        "expected_calendar_hour_count": int(result[2]),
        "expected_observation_count": int(result[3]),
        "duration_seconds": round(perf_counter() - started, 2),
    }
    context.log.info("HOSTRADA calendar reference: %s", metadata)

    yield dg.Output(
        None,
        output_name="plr_reference",
        metadata={**metadata, "row_count": int(result[4])},
    )
    yield dg.Output(
        None,
        output_name="berlin_reference",
        metadata={**metadata, "row_count": int(result[5])},
    )


HOSTRADA_REFERENCE_ASSETS = [
    analytical_hostrada_hourly_reference,
]
