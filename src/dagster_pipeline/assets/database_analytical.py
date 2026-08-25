import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_bridge import (
    NORMALIZED_ICON_PLR_BRIDGE_KEY,
)
from src.dagster_pipeline.assets.database_population import (
    NORMALIZED_POPULATION_KEY,
    REJECTED_POPULATION_KEY,
)
from src.dagster_pipeline.assets.database_weather_normalized import (
    NORMALIZED_ICON_D2_RUC_WEATHER_KEY,
)
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.retention.weather_raw import prune_forecast_data


ANALYTICAL_PLR_WEATHER_KEY = dg.AssetKey(
    ["analytical", "plr_weather"]
)
ANALYTICAL_PLR_WEATHER_POPULATION_KEY = dg.AssetKey(
    ["analytical", "plr_weather_population"]
)


@dg.asset(
    key=ANALYTICAL_PLR_WEATHER_KEY,
    partitions_def=WEATHER_PARTITIONS,
    deps=[
        NORMALIZED_ICON_D2_RUC_WEATHER_KEY,
        NORMALIZED_ICON_PLR_BRIDGE_KEY,
    ],
    group_name="analytical",
    description=(
        "PostgreSQL area-weights cell-level temperature and shade "
        "apparent temperature onto all Berlin PLRs using "
        "fraction_of_plr."
    ),
)
def analytical_plr_weather(
    context: dg.AssetExecutionContext,
) -> None:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_plr_weather"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM analytical.refresh_plr_weather(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (
                forecast.run_time,
                forecast.lead_time_label,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError(
            "PLR weather SQL returned no result"
        )

    metadata = {
        "accepted": bool(result[0]),
        "plr_row_count": int(result[1]),
        "expected_plr_count": int(result[2]),
        "source_grid_id": (
            None if result[3] is None else str(result[3])
        ),
        "geography_version": (
            None if result[4] is None else str(result[4])
        ),
        "rejection_reason": (
            None if result[5] is None else str(result[5])
        ),
    }

    if not bool(result[0]):
        raise RuntimeError(
            "PostgreSQL rejected PLR weather transformation: "
            f"{metadata}"
        )

    context.add_output_metadata(metadata)


@dg.asset(
    key=ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
    partitions_def=WEATHER_PARTITIONS,
    deps=[
        ANALYTICAL_PLR_WEATHER_KEY,
        NORMALIZED_POPULATION_KEY,
        REJECTED_POPULATION_KEY,
    ],
    group_name="analytical",
    description=(
        "Final Berlin PLR temperature, shade apparent temperature, "
        "and 65+ population dataset, joined in PostgreSQL."
    ),
)
def analytical_plr_weather_population(
    context: dg.AssetExecutionContext,
) -> None:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_weather_population"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM analytical.refresh_plr_weather_population(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (
                forecast.run_time,
                forecast.lead_time_label,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError(
            "Weather/population SQL returned no result"
        )

    metadata = {
        "accepted": bool(result[0]),
        "final_row_count": int(result[1]),
        "available_population_count": int(result[2]),
        "rejected_population_count": int(result[3]),
        "population_reference_date": (
            None if result[4] is None else str(result[4])
        ),
        "rejection_reason": (
            None if result[5] is None else str(result[5])
        ),
    }

    if not bool(result[0]):
        raise RuntimeError(
            "PostgreSQL rejected final analytical partition: "
            f"{metadata}"
        )

    retention = prune_forecast_data(dry_run=False)
    metadata.update(
        {
            "forecast_retention_hours": retention.retention_hours,
            "expired_forecast_rows_deleted": retention.database.total_affected_rows,
            "expired_forecast_files_deleted": retention.raw_files.deleted_file_count,
        }
    )
    context.add_output_metadata(metadata)


ANALYTICAL_ASSETS = [
    analytical_plr_weather,
    analytical_plr_weather_population,
]
