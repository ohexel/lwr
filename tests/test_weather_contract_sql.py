from src.database.connection import database_connection


FORBIDDEN_DOWNSTREAM_COLUMNS = {
    "relative_humidity_percent",
    "dew_point_temperature_c",
    "wind_u_10m_ms",
    "wind_v_10m_ms",
    "wind_speed_10m_ms",
}


def _columns(connection, *, schema_name: str, table_name: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        """,
        (schema_name, table_name),
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_weather_relations_do_not_carry_helper_fields_downstream() -> None:
    with database_connection(
        application_name="capstone_weather_contract_test"
    ) as connection:
        normalized_columns = _columns(
            connection,
            schema_name="normalized",
            table_name="icon_d2_ruc_weather",
        )
        analytical_columns = _columns(
            connection,
            schema_name="analytical",
            table_name="plr_weather",
        )
        final_columns = _columns(
            connection,
            schema_name="analytical",
            table_name="plr_weather_population",
        )
        td_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM raw.icon_d2_ruc_source
            WHERE indicator = 'TD_2M'
            """
        ).fetchone()

    for columns in (normalized_columns, analytical_columns, final_columns):
        assert {
            "temperature_c",
            "apparent_temperature_shade_c",
        }.issubset(columns)
        assert FORBIDDEN_DOWNSTREAM_COLUMNS.isdisjoint(columns)

    assert td_count == (0,)
