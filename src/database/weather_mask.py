import os
from dataclasses import dataclass

from psycopg import Connection

from src.database.spatial_state import (
    current_geography_version,
)
from src.icon_grid_contract import (
    ICON_D2_GRID_CONTRACT,
)


DEFAULT_WEATHER_MASK_BUFFER_M = 5000
WEATHER_MASK_BUFFER_M_ENV = "WEATHER_MASK_BUFFER_M"


@dataclass(frozen=True)
class WeatherMaskState:
    geography_version: str
    source_grid_id: str
    mask_buffer_m: int
    mask_cell_count: int


def weather_mask_buffer_m() -> int:
    raw_value = os.getenv(
        WEATHER_MASK_BUFFER_M_ENV,
        str(DEFAULT_WEATHER_MASK_BUFFER_M),
    )
    value = int(raw_value)

    if value < 0:
        raise ValueError(
            f"{WEATHER_MASK_BUFFER_M_ENV} "
            "must be non-negative"
        )

    return value


def current_weather_mask(
    connection: Connection,
    *,
    source_grid_id: str = ICON_D2_GRID_CONTRACT.source_grid_id,
    mask_buffer_m: int | None = None,
) -> WeatherMaskState:
    buffer_m = (
        weather_mask_buffer_m()
        if mask_buffer_m is None
        else mask_buffer_m
    )

    geography_version = current_geography_version(
        connection
    )

    row = connection.execute(
        """
        SELECT
            COUNT(*)::BIGINT AS mask_cell_count
        FROM normalized.icon_weather_mask
        WHERE geography_version = %s
          AND source_grid_id = %s
          AND mask_buffer_m = %s
        """,
        (
            geography_version,
            source_grid_id,
            buffer_m,
        ),
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "Weather-mask count query returned no row"
        )

    mask_cell_count = int(row[0])

    if mask_cell_count == 0:
        raise RuntimeError(
            "No materialized ICON weather mask found for "
            f"geography_version={geography_version}, "
            f"grid={source_grid_id}, "
            f"buffer_m={buffer_m}"
        )

    return WeatherMaskState(
        geography_version=geography_version,
        source_grid_id=source_grid_id,
        mask_buffer_m=buffer_m,
        mask_cell_count=mask_cell_count,
    )
