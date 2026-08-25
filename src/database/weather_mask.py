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
    cell_indices: tuple[int, ...]


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

    rows = connection.execute(
        """
        SELECT
            cell_index
        FROM normalized.icon_weather_mask
        WHERE geography_version = %s
          AND source_grid_id = %s
          AND mask_buffer_m = %s
        ORDER BY cell_index
        """,
        (
            geography_version,
            source_grid_id,
            buffer_m,
        ),
    ).fetchall()

    cell_indices = tuple(int(row[0]) for row in rows)
    mask_cell_count = len(cell_indices)

    if mask_cell_count == 0:
        raise RuntimeError(
            "No materialized ICON weather mask found for "
            f"geography_version={geography_version}, "
            f"grid={source_grid_id}, "
            f"buffer_m={buffer_m}"
        )

    if len(set(cell_indices)) != mask_cell_count:
        raise RuntimeError("Materialized ICON weather mask contains duplicates")

    invalid_indices = [
        cell_index
        for cell_index in cell_indices
        if not 0 <= cell_index < ICON_D2_GRID_CONTRACT.field_point_count
    ]
    if invalid_indices:
        raise RuntimeError(
            "Materialized ICON weather mask contains out-of-range cell indices"
        )

    return WeatherMaskState(
        geography_version=geography_version,
        source_grid_id=source_grid_id,
        mask_buffer_m=buffer_m,
        mask_cell_count=mask_cell_count,
        cell_indices=cell_indices,
    )
