from unittest.mock import Mock

from src.database.weather_mask import (
    current_weather_mask,
)


def test_current_weather_mask_counts_mask_rows_once() -> None:
    connection = Mock()

    geography_cursor = Mock()
    geography_cursor.fetchone.return_value = (
        "2023-01-01",
    )

    count_cursor = Mock()
    count_cursor.fetchone.return_value = (465,)

    connection.execute.side_effect = [
        geography_cursor,
        count_cursor,
    ]

    state = current_weather_mask(
        connection,
        source_grid_id="icon_grid_0047_R19B07_L",
        mask_buffer_m=5000,
    )

    assert state.geography_version == "2023-01-01"
    assert state.mask_cell_count == 465

    count_query = connection.execute.call_args_list[
        1
    ].args[0]

    assert "current_plr_version" not in count_query
    assert "JOIN normalized.plr" not in count_query
