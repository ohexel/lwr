from src.database.connection import database_connection


def test_latest_partition_builds_final_analytical_sql() -> None:
    with database_connection(
        application_name="capstone_analytical_weather_test"
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

        metric_completeness = connection.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE plr_weather.apparent_temperature_shade_c
                        IS NULL
                ),
                COUNT(*)
            FROM analytical.plr_weather AS plr_weather
            WHERE plr_weather.run_time_utc = %s
              AND plr_weather.lead_time = %s
            """,
            (run_time_utc, lead_time),
        ).fetchone()

        assert metric_completeness is not None
        assert int(metric_completeness[0]) == 0
        assert int(metric_completeness[1]) == 542

        serving_view = connection.execute(
            """
            SELECT COUNT(*)
            FROM analytical.current_plr_weather_population AS current_row
            WHERE current_row.run_time_utc = %s
              AND current_row.lead_time = %s
              AND current_row.apparent_temperature_shade_c IS NOT NULL
              AND current_row.apparent_temperature_delta_c
                    IS NOT DISTINCT FROM (
                        current_row.apparent_temperature_shade_c
                        - current_row.temperature_c
                    )
            """,
            (run_time_utc, lead_time),
        ).fetchone()

        assert serving_view is not None
        assert int(serving_view[0]) == 542

        connection.rollback()
