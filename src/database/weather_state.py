import json
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from src.database.connection import database_connection
from src.forecast_key import ForecastKey


@dataclass(frozen=True)
class RawWeatherPartitionState:
    passed: bool
    source_indicator_count: int
    field_indicator_count: int
    total_retained_row_count: int
    expected_retained_row_count: int
    mask_cell_count: int
    missing_indicator_count: int
    unexpected_indicator_count: int
    wrong_source_point_count_indicator_count: int
    wrong_retained_row_count_indicator_count: int
    wrong_valid_time_indicator_count: int
    inconsistent_scope_count: int
    outside_mask_row_count: int
    null_retained_value_count: int
    per_indicator_row_counts: dict[str, int]


def _json_object(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        return {
            str(key): int(item)
            for key, item in value.items()
        }
    if isinstance(value, str):
        decoded = json.loads(value)
        return {
            str(key): int(item)
            for key, item in decoded.items()
        }
    raise TypeError(
        'Expected PostgreSQL JSON object for per_indicator_row_counts'
    )


def query_raw_weather_partition_state(
    connection: Connection,
    forecast: ForecastKey,
) -> RawWeatherPartitionState:
    result = connection.execute(
        '''
        SELECT
            quality.passed,
            quality.source_indicator_count,
            quality.field_indicator_count,
            quality.total_retained_row_count,
            quality.expected_retained_row_count,
            quality.mask_cell_count,
            quality.missing_indicator_count,
            quality.unexpected_indicator_count,
            quality.wrong_source_point_count_indicator_count,
            quality.wrong_retained_row_count_indicator_count,
            quality.wrong_valid_time_indicator_count,
            quality.inconsistent_scope_count,
            quality.outside_mask_row_count,
            quality.null_retained_value_count,
            quality.per_indicator_row_counts
        FROM raw.check_icon_d2_ruc_field_partition(
            %s::TIMESTAMPTZ,
            %s::TEXT,
            %s::TIMESTAMPTZ
        ) AS quality
        ''',
        (
            forecast.run_time,
            forecast.lead_time_label,
            forecast.valid_time,
        ),
    ).fetchone()

    if result is None:
        raise RuntimeError(
            'Raw weather partition SQL check returned no result'
        )

    return RawWeatherPartitionState(
        passed=bool(result[0]),
        source_indicator_count=int(result[1]),
        field_indicator_count=int(result[2]),
        total_retained_row_count=int(result[3]),
        expected_retained_row_count=int(result[4]),
        mask_cell_count=int(result[5]),
        missing_indicator_count=int(result[6]),
        unexpected_indicator_count=int(result[7]),
        wrong_source_point_count_indicator_count=int(result[8]),
        wrong_retained_row_count_indicator_count=int(result[9]),
        wrong_valid_time_indicator_count=int(result[10]),
        inconsistent_scope_count=int(result[11]),
        outside_mask_row_count=int(result[12]),
        null_retained_value_count=int(result[13]),
        per_indicator_row_counts=_json_object(result[14]),
    )


def raw_weather_partition_loaded(
    forecast: ForecastKey,
) -> bool:
    with database_connection(
        application_name='capstone_weather_state'
    ) as connection:
        state = query_raw_weather_partition_state(
            connection,
            forecast,
        )
    return state.passed
