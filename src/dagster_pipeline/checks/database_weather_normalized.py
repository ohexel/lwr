import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_weather_normalized import (
    NORMALIZED_ICON_D2_RUC_WEATHER_KEY,
)
from src.dagster_pipeline.partitions import (
    forecast_key_from_partition,
)


@dg.asset_check(
    asset=NORMALIZED_ICON_D2_RUC_WEATHER_KEY,
    name="icon_d2_ruc_weather_quality",
)
def icon_d2_ruc_weather_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_weather_normalized_check"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM normalized.check_icon_d2_ruc_weather_quality(
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
                "Normalized weather SQL check returned no result."
            ),
        )

    metadata = {
        "normalized_row_count": int(result[1]),
        "expected_mask_cell_count": int(result[2]),
        "bridge_cell_count": int(result[3]),
        "missing_mask_cell_count": int(result[4]),
        "outside_mask_cell_count": int(result[5]),
        "bridge_missing_value_count": int(result[6]),
        "conversion_mismatch_count": int(result[7]),
        "rejected_partition_count": int(result[8]),
    }

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
        description=(
            "PostgreSQL verifies mask coverage, bridge-cell "
            "completeness, and exact source-to-normalized "
            "unit conversion."
        ),
    )


WEATHER_NORMALIZED_CHECKS = [
    icon_d2_ruc_weather_quality,
]
