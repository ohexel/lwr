import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_weather_raw import (
    RAW_ICON_D2_RUC_FIELD_KEY,
)
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)


NORMALIZED_ICON_D2_RUC_WEATHER_KEY = dg.AssetKey(
    ["normalized", "icon_d2_ruc_weather"]
)


@dg.asset(
    key=NORMALIZED_ICON_D2_RUC_WEATHER_KEY,
    partitions_def=WEATHER_PARTITIONS,
    deps=[RAW_ICON_D2_RUC_FIELD_KEY],
    group_name="normalized",
    description=(
        "SQL-normalized Berlin-scoped ICON D2 RUC weather: "
        "four required source fields are combined at cell grain; "
        "temperature and shade apparent temperature are retained, "
        "while humidity and wind remain raw replay inputs only."
    ),
)
def normalized_icon_d2_ruc_weather(
    context: dg.AssetExecutionContext,
) -> None:
    forecast = forecast_key_from_partition(
        context.partition_key
    )

    with database_connection(
        application_name="capstone_weather_normalize"
    ) as connection:
        result = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_icon_d2_ruc_weather(
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
            "Weather normalization SQL returned no result"
        )

    metadata = {
        "accepted": bool(result[0]),
        "normalized_row_count": int(result[1]),
        "expected_mask_cell_count": int(result[2]),
        "bridge_cell_count": int(result[3]),
        "bridge_missing_value_count": int(result[4]),
        "invalid_unit_indicator_count": int(result[5]),
        "rejection_reason": (
            None if result[6] is None else str(result[6])
        ),
    }

    context.log.info(
        "Normalized weather SQL result: %s",
        metadata,
    )

    if not bool(result[0]):
        raise RuntimeError(
            "PostgreSQL rejected normalized weather partition: "
            f"{metadata}"
        )

    context.add_output_metadata(metadata)


WEATHER_NORMALIZED_ASSETS = [
    normalized_icon_d2_ruc_weather,
]
