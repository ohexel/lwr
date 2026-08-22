import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import (
    current_geography_version,
)
from src.database.weather_mask import weather_mask_buffer_m
from src.dagster_pipeline.assets.database_bridge import (
    NORMALIZED_ICON_PLR_BRIDGE_KEY,
)
from src.dagster_pipeline.assets.database_spatial import (
    NORMALIZED_ICON_CELL_KEY,
    NORMALIZED_PLR_KEY,
)
from src.ingestion.icon_grid import ICON_GRID_ID


NORMALIZED_ICON_WEATHER_MASK_KEY = dg.AssetKey(
    ['normalized', 'icon_weather_mask']
)


@dg.asset(
    key=NORMALIZED_ICON_WEATHER_MASK_KEY,
    deps=[
        NORMALIZED_PLR_KEY,
        NORMALIZED_ICON_CELL_KEY,
        NORMALIZED_ICON_PLR_BRIDGE_KEY,
    ],
    group_name='normalized',
    description=(
        'Versioned ICON-D2 cell mask covering Berlin PLRs plus a '
        'configurable spatial safety buffer.'
    ),
)
def normalized_icon_weather_mask(
    context: dg.AssetExecutionContext,
) -> None:
    buffer_m = weather_mask_buffer_m()

    with database_connection(
        application_name='capstone_icon_weather_mask'
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            '''
            SELECT *
            FROM normalized.refresh_icon_weather_mask(
                %s::TEXT,
                %s::TEXT,
                %s::INTEGER
            )
            ''',
            (geography_version, ICON_GRID_ID, buffer_m),
        ).fetchone()

    if result is None:
        raise RuntimeError('Weather-mask refresh returned no result')

    context.add_output_metadata(
        {
            'geography_version': geography_version,
            'source_grid_id': ICON_GRID_ID,
            'mask_buffer_m': buffer_m,
            'mask_cell_count': int(result[0]),
            'bridge_cell_count': int(result[1]),
            'missing_bridge_cell_count': int(result[2]),
        }
    )


ARCHITECTURE_3_WEATHER_MASK_ASSETS = [
    normalized_icon_weather_mask,
]
