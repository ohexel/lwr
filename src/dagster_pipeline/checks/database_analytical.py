import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_analytical import (
    ANALYTICAL_PLR_WEATHER_KEY,
    ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
)
from src.dagster_pipeline.partitions import (
    forecast_key_from_partition,
)


@dg.asset_check(
    asset=ANALYTICAL_PLR_WEATHER_KEY,
    name="plr_weather_quality",
)
def plr_weather_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_plr_weather_check"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM analytical.check_plr_weather_quality(
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
        return dg.AssetCheckResult(
            passed=False,
            description="PLR weather SQL check returned no result.",
        )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata={
            "plr_row_count": int(result[1]),
            "source_plr_count": int(result[2]),
            "missing_plr_count": int(result[3]),
            "null_metric_row_count": int(result[4]),
            "normalized_weather_passed": bool(result[5]),
        },
    )


@dg.asset_check(
    asset=ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
    name="plr_weather_population_quality",
)
def plr_weather_population_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_weather_population_check"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM analytical.check_plr_weather_population_quality(
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
        return dg.AssetCheckResult(
            passed=False,
            description=(
                "Final analytical SQL check returned no result."
            ),
        )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata={
            "final_row_count": int(result[1]),
            "available_count": int(result[2]),
            "rejected_source_record_count": int(result[3]),
            "available_missing_population_metric_count": int(result[4]),
            "rejected_with_population_metric_count": int(result[5]),
            "rejected_missing_reason_count": int(result[6]),
            "available_with_rejection_reason_count": int(result[7]),
            "rejected_registry_mismatch_count": int(result[8]),
            "analytical_rejection_count": int(result[9]),
            "plr_weather_passed": bool(result[10]),
        },
        description=(
            "PostgreSQL verifies complete PLR coverage and that "
            "population exceptions exactly match the normalized "
            "rejection registry."
        ),
    )


ANALYTICAL_CHECKS = [
    plr_weather_quality,
    plr_weather_population_quality,
]
