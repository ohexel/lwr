import dagster as dg

from src.build_plr_weather import (
    EXPECTED_PLR_COUNT,
    build_plr_weather,
    write_plr_weather,
)
from src.dagster_pipeline.assets.icon_d2_ruc import (
    NORMALIZED_ICON_D2_RUC_ASSETS,
)
from src.dagster_pipeline.assets.icon_plr_area_bridge import (
    icon_plr_area_bridge,
)
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.forecast_key import ProjectPaths


PLR_WEATHER_GROUP = "analytical_weather"


@dg.asset(
    name="plr_weather",
    deps=[
        *NORMALIZED_ICON_D2_RUC_ASSETS,
        icon_plr_area_bridge,
    ],
    partitions_def=WEATHER_PARTITIONS,
    group_name=PLR_WEATHER_GROUP,
    compute_kind="python",
    description=(
        "Area-weighted ICON D2 RUC weather "
        "for all Berlin Planungsräume."
    ),
)
def plr_weather(
    context: dg.AssetExecutionContext,
) -> dg.MaterializeResult:
    partition_key = context.partition_key

    if not isinstance(
        partition_key,
        dg.MultiPartitionKey,
    ):
        raise ValueError(
            "plr_weather requires the "
            "run_time × lead_time partition"
        )

    forecast = (
        forecast_key_from_partition(
            partition_key
        )
    )

    paths = ProjectPaths()

    output_path = (
        paths.analytical_plr_weather(
            forecast=forecast
        )
    )

    frame = build_plr_weather(
        forecast=forecast
    )

    write_plr_weather(
        frame=frame,
        path=output_path,
    )

    missing_counts = {
        column: int(
            frame[column].isna().sum()
        )
        for column in [
            "temperature_c",
            "relative_humidity_percent",
            "dew_point_temperature_c",
            "wind_u_10m_ms",
            "wind_v_10m_ms",
            "wind_speed_10m_ms",
        ]
    }

    return dg.MaterializeResult(
        metadata={
            "run_time": (
                forecast.run_time.isoformat()
            ),
            "lead_time": (
                forecast.lead_time_label
            ),
            "valid_time": (
                forecast.valid_time.isoformat()
            ),
            "path": str(output_path),
            "plr_count": len(frame),
            "expected_plr_count": (
                EXPECTED_PLR_COUNT
            ),
            "missing_counts": (
                dg.MetadataValue.json(
                    missing_counts
                )
            ),
        }
    )
