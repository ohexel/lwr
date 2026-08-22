from src.database.connection import database_connection


def _single_value(
    connection,
    query: str,
) -> str:
    rows = connection.execute(query).fetchall()
    values = [
        str(row[0])
        for row in rows
        if row[0] is not None
    ]
    assert len(values) == 1, values
    return values[0]


def test_icon_plr_area_bridge_quality_passes() -> None:
    with database_connection(
        application_name="capstone_test_icon_plr_bridge"
    ) as connection:
        geography_version = _single_value(
            connection,
            """
            SELECT DISTINCT geography_version
            FROM normalized.plr
            ORDER BY geography_version
            """,
        )

        source_grid_id = _single_value(
            connection,
            """
            SELECT DISTINCT source_grid_id
            FROM normalized.icon_cell
            ORDER BY source_grid_id
            """,
        )

        result = connection.execute(
            """
            SELECT *
            FROM normalized.check_icon_plr_area_bridge_quality(
                %s,
                %s
            )
            """,
            (geography_version, source_grid_id),
        ).fetchone()

    assert result is not None
    assert result[0] is True, result
    assert int(result[1]) > 0
    assert int(result[2]) == 542
    assert int(result[3]) == 542
    assert int(result[4]) > 0
    assert int(result[5]) == 0
    assert int(result[10]) == 0
