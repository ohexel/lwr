from src.database.connection import database_connection
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT


SERVING_COLUMNS = [
    "plr_id",
    "plr_name",
    "run_time_utc",
    "lead_time",
    "valid_time_utc",
    "valid_time_berlin",
    "temperature_c",
    "apparent_temperature_shade_c",
    "plr_temperature_median_c",
    "plr_temperature_p90_c",
    "plr_temperature_max_c",
    "plr_apparent_temperature_median_c",
    "plr_apparent_temperature_p90_c",
    "plr_apparent_temperature_max_c",
    "berlin_temperature_median_c",
    "berlin_temperature_p90_c",
    "berlin_temperature_max_c",
    "berlin_apparent_temperature_median_c",
    "berlin_apparent_temperature_p90_c",
    "berlin_apparent_temperature_max_c",
    "population_total",
    "population_65plus",
    "population_status",
]


def test_hostrada_expected_calendar_hours_cover_complete_reference_period():
    with database_connection(
        application_name="capstone_hostrada_reference_calendar_test"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                calendar_month.month,
                COUNT(*)::INTEGER,
                SUM(expected_hour.sample_count)::INTEGER
            FROM generate_series(1, 12) AS calendar_month(month)
            CROSS JOIN LATERAL
                analytical.hostrada_reference_expected_hours(
                    calendar_month.month
                ) AS expected_hour
            GROUP BY calendar_month.month
            ORDER BY calendar_month.month
            """
        ).fetchall()

    assert observed == [
        (1, 744, 23063),
        (2, 672, 20832),
        (3, 744, 23033),
        (4, 720, 22320),
        (5, 744, 23064),
        (6, 720, 22320),
        (7, 744, 23064),
        (8, 744, 23064),
        (9, 720, 22321),
        (10, 744, 23094),
        (11, 720, 22320),
        (12, 744, 23064),
    ]
    assert sum(row[1] for row in observed) == 8760
    assert sum(row[2] for row in observed) == 271559


def test_hostrada_calendar_preserves_boundary_and_historical_dst_rules():
    with database_connection(
        application_name="capstone_hostrada_reference_dst_test"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                expected_hour.calendar_month,
                expected_hour.calendar_day,
                expected_hour.local_hour,
                expected_hour.sample_count
            FROM generate_series(1, 12) AS calendar_month(month)
            CROSS JOIN LATERAL
                analytical.hostrada_reference_expected_hours(
                    calendar_month.month
                ) AS expected_hour
            WHERE (
                expected_hour.calendar_month,
                expected_hour.calendar_day,
                expected_hour.local_hour
            ) IN (
                (1, 1, 0),
                (3, 26, 2),
                (9, 24, 2),
                (10, 26, 2)
            )
            ORDER BY 1, 2, 3
            """
        ).fetchall()

    assert observed == [
        (1, 1, 0, 30),
        (3, 26, 2, 26),
        (9, 24, 2, 32),
        (10, 26, 2, 36),
    ]


def test_hostrada_reference_quality_rejects_unknown_geography():
    with database_connection(
        application_name="capstone_hostrada_reference_quality_test"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                quality.passed,
                quality.expected_plr_count,
                quality.expected_calendar_hour_count,
                quality.plr_reference_count,
                quality.berlin_reference_count
            FROM analytical.check_hostrada_reference_month_quality(
                1,
                '__missing_hostrada_test_geography__',
                %s::TEXT
            ) AS quality
            """,
            (HOSTRADA_GRID_CONTRACT.source_grid_id,),
        ).fetchone()

    assert observed == (False, 0, 744, 0, 0)


def test_hostrada_serving_views_expose_exact_lean_column_contract():
    with database_connection(
        application_name="capstone_hostrada_reference_serving_test"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                table_name,
                column_name
            FROM information_schema.columns
            WHERE table_schema = 'analytical'
              AND table_name IN (
                  'plr_weather_context',
                  'current_plr_weather_context'
              )
            ORDER BY
                table_name,
                ordinal_position
            """
        ).fetchall()

    columns = {
        table_name: [
            column_name
            for observed_table, column_name in observed
            if observed_table == table_name
        ]
        for table_name in (
            "plr_weather_context",
            "current_plr_weather_context",
        )
    }

    assert columns["plr_weather_context"] == SERVING_COLUMNS
    assert columns["current_plr_weather_context"] == SERVING_COLUMNS


def test_hostrada_serving_views_preserve_all_forecast_rows():
    with database_connection(
        application_name="capstone_hostrada_reference_left_join_test"
    ) as connection:
        observed = connection.execute(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM analytical.plr_weather_context
                ),
                (
                    SELECT COUNT(*)
                    FROM analytical.plr_weather_population
                ),
                (
                    SELECT COUNT(*)
                    FROM analytical.current_plr_weather_context
                ),
                (
                    SELECT COUNT(*)
                    FROM analytical.current_plr_weather_population
                )
            """
        ).fetchone()

    assert observed is not None
    assert observed[0] == observed[1]
    assert observed[2] == observed[3]
