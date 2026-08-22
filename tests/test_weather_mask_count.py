from unittest.mock import Mock

from src.database.weather_mask import current_weather_mask


def test_current_weather_mask_does_not_multiply_by_plr_rows() -> None:
    connection = Mock()
    cursor = Mock()
    cursor.fetchone.return_value = (
        "2023-01-01",
        "icon_grid_0047_R19B07_L",
        5000,
        465,
    )
    connection.execute.return_value = cursor

    state = current_weather_mask(
        connection,
        source_grid_id="icon_grid_0047_R19B07_L",
        mask_buffer_m=5000,
    )

    assert state.mask_cell_count == 465

    query = connection.execute.call_args.args[0]

    assert "current_plr_version" in query
    assert (
        "LEFT JOIN normalized.plr AS plr_row"
        not in query
    )
