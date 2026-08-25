import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np

from src.database.connection import database_connection
from src.database.load import copy_rows
from src.database.weather_mask import current_weather_mask
from src.database.weather_state import query_raw_weather_partition_state
from src.dwd_icon_d2_ruc import field_url, sha256_file
from src.forecast_key import ForecastKey, ProjectPaths
from src.icon_d2_ruc_grib import decode_and_validate_field
from src.icon_d2_ruc_indicators import INDICATORS
from src.icon_grid_contract import (
    ICON_D2_GRID_CONTRACT,
)


REQUIRED_INDICATORS = tuple(INDICATORS)


@dataclass(frozen=True)
class WeatherRawLoadResult:
    run_time_utc: str
    lead_time: str
    valid_time_utc: str
    indicator_count: int
    source_row_count: int
    retained_row_count: int
    mask_cell_count: int
    source_missing_value_count: int
    retained_missing_value_count: int
    load_seconds: float


@dataclass(frozen=True)
class PreparedField:
    indicator: str
    source_path: Path
    source_url: str
    source_sha256: str
    source_unit: str
    values: np.ndarray


def _source_path(
    paths: ProjectPaths,
    *,
    indicator: str,
    forecast: ForecastKey,
) -> Path:
    return paths.raw_icon_field(
        indicator=indicator,
        forecast=forecast,
    )


def _quarantine_path(
    *,
    paths: ProjectPaths,
    forecast: ForecastKey,
    indicator: str,
) -> Path:
    return (
        paths.data_root
        / 'raw'
        / 'quarantine'
        / 'icon_d2_ruc'
        / forecast.run_label
        / forecast.lead_time_label
        / indicator.lower()
        / 'failure.json'
    )


def _write_decode_failure(
    *,
    paths: ProjectPaths,
    forecast: ForecastKey,
    indicator: str,
    source_path: Path,
    error: Exception,
) -> None:
    target = _quarantine_path(
        paths=paths,
        forecast=forecast,
        indicator=indicator,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'run_time_utc': forecast.run_time.isoformat(),
        'lead_time': forecast.lead_time_label,
        'valid_time_utc': forecast.valid_time.isoformat(),
        'indicator': indicator,
        'source_path': str(source_path),
        'source_sha256': (
            sha256_file(source_path)
            if source_path.exists()
            else None
        ),
        'error_type': type(error).__name__,
        'error': str(error),
    }
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _prepare_field(
    *,
    paths: ProjectPaths,
    forecast: ForecastKey,
    indicator: str,
) -> PreparedField:
    source_path = _source_path(
        paths,
        indicator=indicator,
        forecast=forecast,
    )
    if not source_path.exists():
        raise FileNotFoundError(
            f'Retained raw weather file not found: {source_path}'
        )

    try:
        decoded = decode_and_validate_field(
            path=source_path,
            indicator=indicator,
            forecast=forecast,
            expected_point_count=ICON_D2_GRID_CONTRACT.field_point_count,
        )
    except Exception as exc:
        _write_decode_failure(
            paths=paths,
            forecast=forecast,
            indicator=indicator,
            source_path=source_path,
            error=exc,
        )
        raise

    values = np.asarray(
        decoded.values,
        dtype='float64',
    ).reshape(-1)

    if len(values) != ICON_D2_GRID_CONTRACT.field_point_count:
        error = ValueError(
            f'{indicator} decoded {len(values):,} values; expected '
            f'{ICON_D2_GRID_CONTRACT.field_point_count:,}'
        )
        _write_decode_failure(
            paths=paths,
            forecast=forecast,
            indicator=indicator,
            source_path=source_path,
            error=error,
        )
        raise error

    source_unit = str(decoded.metadata.get('units', '')).strip()
    if not source_unit:
        error = ValueError(
            f'{indicator} decoded metadata contains no source unit'
        )
        _write_decode_failure(
            paths=paths,
            forecast=forecast,
            indicator=indicator,
            source_path=source_path,
            error=error,
        )
        raise error

    return PreparedField(
        indicator=indicator,
        source_path=source_path,
        source_url=field_url(indicator, forecast),
        source_sha256=sha256_file(source_path),
        source_unit=source_unit,
        values=values,
    )


def _masked_stage_rows(
    values: np.ndarray,
    cell_indices: tuple[int, ...],
):
    """Yield only verified Berlin-mask values from a validated full grid."""
    for cell_index in cell_indices:
        if not 0 <= cell_index < len(values):
            raise ValueError(
                f'Weather-mask cell index {cell_index:,} is outside the '
                f'decoded field of {len(values):,} values'
            )
        source_value = values[cell_index]
        yield (
            int(cell_index),
            None if np.isnan(source_value) else float(source_value),
        )


def load_icon_d2_ruc_raw_partition(
    forecast: ForecastKey,
    *,
    paths: ProjectPaths | None = None,
) -> WeatherRawLoadResult:
    # Validate each complete source field, but cross the Python/PostgreSQL
    # boundary only with the small, already materialized Berlin mask.
    started = perf_counter()
    project_paths = paths if paths is not None else ProjectPaths()

    for indicator in REQUIRED_INDICATORS:
        source_path = _source_path(
            project_paths,
            indicator=indicator,
            forecast=forecast,
        )
        if not source_path.exists():
            raise FileNotFoundError(
                f'Retained raw weather file not found: {source_path}'
            )

    source_missing_value_count = 0
    retained_missing_value_count = 0
    retained_row_count = 0

    with database_connection(
        application_name='capstone_icon_d2_ruc_raw_load'
    ) as connection:
        mask = current_weather_mask(
            connection,
            source_grid_id=ICON_D2_GRID_CONTRACT.source_grid_id,
        )

        # The manifest is the parent of retained field rows, so this single
        # delete cascades the previous partition replacement.
        connection.execute(
            '''
            DELETE FROM raw.icon_d2_ruc_source
            WHERE run_time_utc = %s
              AND lead_time = %s
            ''',
            (forecast.run_time, forecast.lead_time_label),
        )

        connection.execute(
            '''
            CREATE TEMP TABLE icon_d2_ruc_field_stage (
                cell_index INTEGER PRIMARY KEY,
                source_value DOUBLE PRECISION
            ) ON COMMIT DROP
            '''
        )

        for indicator in REQUIRED_INDICATORS:
            field = _prepare_field(
                paths=project_paths,
                forecast=forecast,
                indicator=indicator,
            )
            field_missing_count = int(np.isnan(field.values).sum())
            source_missing_value_count += field_missing_count

            connection.execute(
                'TRUNCATE TABLE pg_temp.icon_d2_ruc_field_stage'
            )

            stage_result = copy_rows(
                connection,
                schema='pg_temp',
                table='icon_d2_ruc_field_stage',
                columns=('cell_index', 'source_value'),
                rows=_masked_stage_rows(
                    field.values,
                    mask.cell_indices,
                ),
            )

            if stage_result.row_count != mask.mask_cell_count:
                raise RuntimeError(
                    'Temporary Berlin-mask COPY row count mismatch: '
                    f'expected {mask.mask_cell_count:,}, '
                    f'loaded {stage_result.row_count:,}'
                )

            connection.execute(
                '''
                INSERT INTO raw.icon_d2_ruc_source (
                    run_time_utc,
                    lead_time,
                    indicator,
                    valid_time_utc,
                    source_grid_id,
                    geography_version,
                    mask_buffer_m,
                    source_unit,
                    source_url,
                    raw_path,
                    source_sha256,
                    source_point_count,
                    source_missing_value_count,
                    retained_point_count
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                ''',
                (
                    forecast.run_time,
                    forecast.lead_time_label,
                    indicator,
                    forecast.valid_time,
                    mask.source_grid_id,
                    mask.geography_version,
                    mask.mask_buffer_m,
                    field.source_unit,
                    field.source_url,
                    str(field.source_path),
                    field.source_sha256,
                    ICON_D2_GRID_CONTRACT.field_point_count,
                    field_missing_count,
                    mask.mask_cell_count,
                ),
            )

            insert_result = connection.execute(
                '''
                INSERT INTO raw.icon_d2_ruc_field (
                    run_time_utc,
                    lead_time,
                    indicator,
                    cell_index,
                    source_value
                )
                SELECT
                    %s,
                    %s,
                    %s,
                    stage.cell_index,
                    stage.source_value
                FROM pg_temp.icon_d2_ruc_field_stage AS stage
                JOIN normalized.icon_weather_mask AS mask_row
                  ON mask_row.source_grid_id = %s
                 AND mask_row.geography_version = %s
                 AND mask_row.mask_buffer_m = %s
                 AND mask_row.cell_index = stage.cell_index
                ''',
                (
                    forecast.run_time,
                    forecast.lead_time_label,
                    indicator,
                    mask.source_grid_id,
                    mask.geography_version,
                    mask.mask_buffer_m,
                ),
            )

            indicator_retained_count = int(insert_result.rowcount)
            if indicator_retained_count != mask.mask_cell_count:
                raise RuntimeError(
                    f'{indicator} retained {indicator_retained_count:,} '
                    f'rows; expected mask count {mask.mask_cell_count:,}'
                )

            retained_row_count += indicator_retained_count

            retained_missing = connection.execute(
                '''
                SELECT COUNT(*)
                FROM raw.icon_d2_ruc_field
                WHERE run_time_utc = %s
                  AND lead_time = %s
                  AND indicator = %s
                  AND source_value IS NULL
                ''',
                (
                    forecast.run_time,
                    forecast.lead_time_label,
                    indicator,
                ),
            ).fetchone()
            retained_missing_value_count += int(retained_missing[0])

            del field

        partition_state = query_raw_weather_partition_state(
            connection,
            forecast,
        )
        if not partition_state.passed:
            raise RuntimeError(
                'PostgreSQL rejected Berlin-scoped raw weather '
                f'partition: {partition_state}'
            )

    return WeatherRawLoadResult(
        run_time_utc=forecast.run_time.isoformat(),
        lead_time=forecast.lead_time_label,
        valid_time_utc=forecast.valid_time.isoformat(),
        indicator_count=len(REQUIRED_INDICATORS),
        source_row_count=(
            ICON_D2_GRID_CONTRACT.field_point_count * len(REQUIRED_INDICATORS)
        ),
        retained_row_count=retained_row_count,
        mask_cell_count=partition_state.mask_cell_count,
        source_missing_value_count=source_missing_value_count,
        retained_missing_value_count=retained_missing_value_count,
        load_seconds=perf_counter() - started,
    )
