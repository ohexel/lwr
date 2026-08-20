import dagster as dg

from src.build_plr_weather_population import (
    build_plr_weather_population,
    write_plr_weather_population,
)
from src.dagster_pipeline.assets.plr_weather import (
    plr_weather,
)
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.forecast_key import ProjectPaths


@dg.asset(
    name="plr_weather_population",
    deps=[
        plr_weather,
        dg.AssetKey("plr_population_65plus"),
        dg.AssetKey("plr_population_rejected"),
    ],
    partitions_def=WEATHER_PARTITIONS,
    group_name="analytical_exposure",
    compute_kind="python",
)
def plr_weather_population(
    context: dg.AssetExecutionContext,
) -> dg.MaterializeResult:
    partition_key = context.partition_key

    if not isinstance(
        partition_key,
        dg.MultiPartitionKey,
    ):
        raise ValueError(
            "plr_weather_population requires "
            "run_time × lead_time"
        )

    forecast = (
        forecast_key_from_partition(
            partition_key
        )
    )

    paths = ProjectPaths()

    frame = build_plr_weather_population(
        forecast=forecast
    )

    output_path = (
        paths.analytical_plr_weather_population(
            forecast=forecast
        )
    )

    write_plr_weather_population(
        frame=frame,
        path=output_path,
    )

    status_counts = {
        str(status): int(count)
        for status, count in frame[
            "population_status"
        ].value_counts().items()
    }

    return dg.MaterializeResult(
        metadata={
            "path": str(output_path),
            "plr_count": len(frame),
            "population_status_counts": (
                dg.MetadataValue.json(
                    status_counts
                )
            ),
            "run_time": (
                forecast.run_time.isoformat()
            ),
            "lead_time": (
                forecast.lead_time_label
            ),
            "valid_time": (
                forecast.valid_time.isoformat()
            ),
        }
    )
