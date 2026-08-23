import dagster as dg

from src.dagster_pipeline.assets.database_weather_mask import (
    NORMALIZED_ICON_WEATHER_MASK_KEY,
)
from src.dagster_pipeline.assets.icon_d2_ruc import RAW_ICON_D2_RUC_ASSETS
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.ingestion.icon_d2_ruc_field import load_icon_d2_ruc_raw_partition


RAW_ICON_D2_RUC_FIELD_KEY = dg.AssetKey(
    ['raw', 'icon_d2_ruc_field']
)


@dg.asset(
    key=RAW_ICON_D2_RUC_FIELD_KEY,
    partitions_def=WEATHER_PARTITIONS,
    deps=[
        *RAW_ICON_D2_RUC_ASSETS,
        NORMALIZED_ICON_WEATHER_MASK_KEY,
    ],
    group_name='raw',
    description=(
        'Source-faithful ICON D2 RUC values retained only for the '
        'versioned Berlin weather mask; full source remains in GRIB.'
    ),
)
def raw_icon_d2_ruc_field(
    context: dg.AssetExecutionContext,
) -> None:
    forecast = forecast_key_from_partition(context.partition_key)

    context.log.info(
        'Loading Berlin-scoped raw ICON D2 RUC forecast %s × %s',
        forecast.run_label,
        forecast.lead_time_label,
    )

    result = load_icon_d2_ruc_raw_partition(forecast)

    context.add_output_metadata(
        {
            'run_time_utc': result.run_time_utc,
            'lead_time': result.lead_time,
            'valid_time_utc': result.valid_time_utc,
            'indicator_count': result.indicator_count,
            'source_row_count': result.source_row_count,
            'retained_row_count': result.retained_row_count,
            'mask_cell_count': result.mask_cell_count,
            'source_missing_value_count': (
                result.source_missing_value_count
            ),
            'retained_missing_value_count': (
                result.retained_missing_value_count
            ),
            'load_seconds': result.load_seconds,
        }
    )


WEATHER_RAW_ASSETS = [
    raw_icon_d2_ruc_field,
]
