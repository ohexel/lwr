from src.database.connection import database_connection


def test_latest_raw_partition_normalizes_in_sql() -> None:
    with database_connection(
        application_name="capstone_test_phase8_weather"
    ) as connection:
        partition = connection.execute(
            """
            SELECT
                source_row.run_time_utc,
                source_row.lead_time
            FROM raw.icon_d2_ruc_source AS source_row
            GROUP BY
                source_row.run_time_utc,
                source_row.lead_time
            HAVING COUNT(*) = 4
            ORDER BY source_row.run_time_utc DESC
            LIMIT 1
            """
        ).fetchone()

        assert partition is not None
        run_time_utc, lead_time = partition

        refresh = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_icon_d2_ruc_weather(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()

        assert refresh is not None
        assert refresh[0] is True, refresh

        quality = connection.execute(
            """
            SELECT *
            FROM normalized.check_icon_d2_ruc_weather_quality(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()

        assert quality is not None
        assert quality[0] is True, quality
        assert int(quality[1]) == int(quality[2])
        assert int(quality[6]) == 0
        assert int(quality[7]) == 0

        fidelity = connection.execute(
            """
            SELECT COUNT(*)
            FROM normalized.icon_d2_ruc_weather AS weather_row
            JOIN raw.icon_d2_ruc_field AS raw_row
              ON raw_row.run_time_utc = weather_row.run_time_utc
             AND raw_row.lead_time = weather_row.lead_time
             AND raw_row.indicator = 'T_2M'
             AND raw_row.cell_index = weather_row.cell_index
            WHERE weather_row.run_time_utc = %s
              AND weather_row.lead_time = %s
              AND weather_row.temperature_c
                    IS DISTINCT FROM raw_row.source_value - 273.15
            """,
            (run_time_utc, lead_time),
        ).fetchone()

        assert fidelity is not None
        assert int(fidelity[0]) == 0

        connection.rollback()
