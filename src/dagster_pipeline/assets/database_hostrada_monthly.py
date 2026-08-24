"""Monthly source provenance and transactional HOSTRADA hourly outputs."""

import dagster as dg

from src.dagster_pipeline.assets.database_hostrada_spatial import (
    NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY,
)
from src.dagster_pipeline.partitions import (
    HOSTRADA_MONTHLY_PARTITIONS,
    hostrada_month_from_partition,
)
from src.ingestion.hostrada_download import download_hostrada_month
from src.ingestion.hostrada_month import (
    load_hostrada_month,
    register_hostrada_month_sources,
)


RAW_HOSTRADA_MONTH_FILES_KEY = dg.AssetKey(
    ["raw", "hostrada_month_files"]
)
RAW_HOSTRADA_MONTH_SOURCE_KEY = dg.AssetKey(
    ["raw", "hostrada_month_source"]
)
ANALYTICAL_HOSTRADA_PLR_HOURLY_KEY = dg.AssetKey(
    ["analytical", "hostrada_plr_hourly"]
)
ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY = dg.AssetKey(
    ["analytical", "hostrada_berlin_hourly"]
)


@dg.asset(
    key=RAW_HOSTRADA_MONTH_FILES_KEY,
    partitions_def=HOSTRADA_MONTHLY_PARTITIONS,
    group_name="hostrada_monthly",
    description=(
        "Download or reuse the three monthly HOSTRADA NetCDF sources "
        "through atomic, streamed, bounded-retry transfers."
    ),
)
def raw_hostrada_month_files(
    context: dg.AssetExecutionContext,
) -> None:
    month = hostrada_month_from_partition(context.partition_key)
    result = download_hostrada_month(month)
    metadata = {
        "source_month": result.source_month,
        "source_file_count": len(result.sources),
        "downloaded_file_count": result.downloaded_file_count,
        "reused_file_count": result.reused_file_count,
        "source_size_bytes": result.source_size_bytes,
        "download_duration_seconds": result.duration_seconds,
    }
    context.log.info("HOSTRADA source acquisition: %s", metadata)
    context.add_output_metadata(metadata)


@dg.asset(
    key=RAW_HOSTRADA_MONTH_SOURCE_KEY,
    deps=[RAW_HOSTRADA_MONTH_FILES_KEY],
    partitions_def=HOSTRADA_MONTHLY_PARTITIONS,
    group_name="hostrada_monthly",
    description=(
        "Three validated local HOSTRADA NetCDF files with persistent "
        "source URLs, checksums, units, UTC coverage, and grid identity."
    ),
)
def raw_hostrada_month_source(
    context: dg.AssetExecutionContext,
) -> None:
    month = hostrada_month_from_partition(context.partition_key)
    result = register_hostrada_month_sources(month)
    metadata = {
        "source_month": result.source_month,
        "source_grid_id": result.source_grid_id,
        "source_file_count": result.source_file_count,
        "source_size_bytes": result.source_size_bytes,
        "source_hour_count": result.source_hour_count,
        "validation_duration_seconds": result.duration_seconds,
    }
    context.log.info("HOSTRADA source manifest: %s", metadata)
    context.add_output_metadata(metadata)


@dg.multi_asset(
    outs={
        "plr_hourly": dg.AssetOut(
            key=ANALYTICAL_HOSTRADA_PLR_HOURLY_KEY
        ),
        "berlin_hourly": dg.AssetOut(
            key=ANALYTICAL_HOSTRADA_BERLIN_HOURLY_KEY
        ),
    },
    deps=[RAW_HOSTRADA_MONTH_SOURCE_KEY, NORMALIZED_HOSTRADA_PLR_BRIDGE_KEY],
    partitions_def=HOSTRADA_MONTHLY_PARTITIONS,
    group_name="hostrada_monthly",
    description=(
        "Stream Berlin-intersecting HOSTRADA cells through temporary "
        "staging and atomically refresh area-weighted PLR and Berlin hours."
    ),
)
def analytical_hostrada_hourly(
    context: dg.AssetExecutionContext,
):
    month = hostrada_month_from_partition(context.partition_key)

    def log_progress(processed_hours: int, total_hours: int) -> None:
        context.log.info(
            "Staged HOSTRADA hours: %s/%s",
            processed_hours,
            total_hours,
        )

    result = load_hostrada_month(month, progress=log_progress)
    metadata = {
        "source_month": result.source_month,
        "source_grid_id": result.source_grid_id,
        "geography_version": result.geography_version,
        "expected_hour_count": result.expected_hour_count,
        "retained_cell_count": result.retained_cell_count,
        "expected_plr_count": result.expected_plr_count,
        "source_cell_hour_count": result.source_cell_hour_count,
        "staging_duration_seconds": result.staging_duration_seconds,
        "transformation_duration_seconds": (
            result.transformation_duration_seconds
        ),
        "total_duration_seconds": result.total_duration_seconds,
        "temporary_stage_table_bytes": result.stage_table_bytes,
        "source_manifest_table_bytes": result.source_manifest_bytes,
        "plr_table_bytes": result.plr_table_bytes,
        "berlin_table_bytes": result.berlin_table_bytes,
    }
    context.log.info("HOSTRADA monthly benchmark: %s", metadata)

    yield dg.Output(
        None,
        output_name="plr_hourly",
        metadata={**metadata, "row_count": result.plr_hour_count},
    )
    yield dg.Output(
        None,
        output_name="berlin_hourly",
        metadata={**metadata, "row_count": result.berlin_hour_count},
    )


HOSTRADA_MONTHLY_ASSETS = [
    raw_hostrada_month_files,
    raw_hostrada_month_source,
    analytical_hostrada_hourly,
]
