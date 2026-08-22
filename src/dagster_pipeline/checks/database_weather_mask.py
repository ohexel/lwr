import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import (
    current_geography_version,
)
from src.database.weather_mask import weather_mask_buffer_m
from src.dagster_pipeline.assets.database_weather_mask import (
    NORMALIZED_ICON_WEATHER_MASK_KEY,
)
from src.ingestion.icon_grid import ICON_GRID_ID


@dg.asset_check(
    asset=NORMALIZED_ICON_WEATHER_MASK_KEY,
    name='icon_weather_mask_quality',
)
def icon_weather_mask_quality(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    buffer_m = weather_mask_buffer_m()

    with database_connection(
        application_name='capstone_icon_weather_mask_check'
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            '''
            SELECT *
            FROM normalized.check_icon_weather_mask_quality(
                %s::TEXT,
                %s::TEXT,
                %s::INTEGER
            )
            ''',
            (geography_version, ICON_GRID_ID, buffer_m),
        ).fetchone()

    if result is None:
        raise RuntimeError('Weather-mask quality check returned no result')

    metadata = {
        'geography_version': geography_version,
        'source_grid_id': ICON_GRID_ID,
        'mask_buffer_m': buffer_m,
        'source_plr_count': int(result[1]),
        'mask_cell_count': int(result[2]),
        'bridge_cell_count': int(result[3]),
        'missing_bridge_cell_count': int(result[4]),
        'orphan_mask_cell_count': int(result[5]),
    }

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata=metadata,
    )


ARCHITECTURE_3_WEATHER_MASK_CHECKS = [
    icon_weather_mask_quality,
]
