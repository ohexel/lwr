from src.database.connection import database_connection


def test_latest_partition_builds_final_analytical_sql() -> None:
    with database_connection(
        application_name="capstone_test_phase9_analytical"
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

        normalized = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_icon_d2_ruc_weather(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()
        assert normalized is not None
        assert normalized[0] is True, normalized

        plr_weather = connection.execute(
            """
            SELECT *
            FROM analytical.refresh_plr_weather(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()
        assert plr_weather is not None
        assert plr_weather[0] is True, plr_weather
        assert int(plr_weather[1]) == 542

        final = connection.execute(
            """
            SELECT *
            FROM analytical.refresh_plr_weather_population(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()
        assert final is not None
        assert final[0] is True, final
        assert int(final[1]) == 542

        quality = connection.execute(
            """
            SELECT *
            FROM analytical.check_plr_weather_population_quality(
                %s::TIMESTAMPTZ,
                %s::TEXT
            )
            """,
            (run_time_utc, lead_time),
        ).fetchone()
        assert quality is not None
        assert quality[0] is True, quality
        assert int(quality[1]) == 542
        assert int(quality[2]) + int(quality[3]) == 542

        connection.rollback()
