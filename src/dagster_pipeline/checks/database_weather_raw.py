import dagster as dg

from src.database.connection import database_connection
from src.database.weather_state import query_raw_weather_partition_state
from src.dagster_pipeline.assets.database_weather_raw import (
    RAW_ICON_D2_RUC_FIELD_KEY,
)
from src.dagster_pipeline.partitions import forecast_key_from_partition


@dg.asset_check(
    asset=RAW_ICON_D2_RUC_FIELD_KEY,
    name='raw_icon_d2_ruc_field_completeness',
)
def raw_icon_d2_ruc_field_completeness(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    forecast = forecast_key_from_partition(context.partition_key)

    with database_connection(
        application_name='capstone_weather_raw_check'
    ) as connection:
        state = query_raw_weather_partition_state(connection, forecast)

    metadata = {
        'run_time_utc': forecast.run_time.isoformat(),
        'lead_time': forecast.lead_time_label,
        'valid_time_utc': forecast.valid_time.isoformat(),
        'source_indicator_count': state.source_indicator_count,
        'field_indicator_count': state.field_indicator_count,
        'total_retained_row_count': state.total_retained_row_count,
        'expected_retained_row_count': state.expected_retained_row_count,
        'mask_cell_count': state.mask_cell_count,
        'missing_indicator_count': state.missing_indicator_count,
        'unexpected_indicator_count': state.unexpected_indicator_count,
        'wrong_source_point_count_indicator_count': (
            state.wrong_source_point_count_indicator_count
        ),
        'wrong_retained_row_count_indicator_count': (
            state.wrong_retained_row_count_indicator_count
        ),
        'wrong_valid_time_indicator_count': (
            state.wrong_valid_time_indicator_count
        ),
        'inconsistent_scope_count': state.inconsistent_scope_count,
        'outside_mask_row_count': state.outside_mask_row_count,
        'null_retained_value_count': state.null_retained_value_count,
        'per_indicator_row_counts': dg.MetadataValue.json(
            state.per_indicator_row_counts
        ),
    }

    return dg.AssetCheckResult(
        passed=state.passed,
        metadata=metadata,
        description=(
            'PostgreSQL validates five permanent source manifests and '
            'the Berlin-scoped source-value projection against the '
            'versioned weather mask used during ingestion.'
        ),
    )


ARCHITECTURE_3_WEATHER_RAW_CHECKS = [
    raw_icon_d2_ruc_field_completeness,
]
