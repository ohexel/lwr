from unittest.mock import Mock

from src.database.weather_mask import (
    current_weather_mask,
)


def test_current_weather_mask_returns_ordered_cell_indices_once() -> None:
    connection = Mock()

    geography_cursor = Mock()
    geography_cursor.fetchone.return_value = (
        "2023-01-01",
    )

    mask_cursor = Mock()
    mask_cursor.fetchall.return_value = [(2,), (7,), (11,)]

    connection.execute.side_effect = [
        geography_cursor,
        mask_cursor,
    ]

    state = current_weather_mask(
        connection,
        source_grid_id="icon_grid_0047_R19B07_L",
        mask_buffer_m=5000,
    )

    assert state.geography_version == "2023-01-01"
    assert state.mask_cell_count == 3
    assert state.cell_indices == (2, 7, 11)

    mask_query = connection.execute.call_args_list[
        1
    ].args[0]

    assert "current_plr_version" not in mask_query
    assert "JOIN normalized.plr" not in mask_query
    assert "ORDER BY cell_index" in mask_query
