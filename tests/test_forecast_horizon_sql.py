"""Exercise real PostgreSQL forecast views using disposable two-PLR fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.database.connection import database_connection


SERVING_SQL = (
    Path(__file__).resolve().parent.parent
    / "sql"
    / "plr_temperature_forecast_25h.sql"
).read_text(encoding="utf-8")

PLR_FIXTURES = (
    ("01100101", "Stülerstraße", 1_000, 700, "available"),
    ("01100102", "Großer Tiergarten", None, None, "rejected_source_record"),
)


def install_temporary_serving_views(connection) -> None:
    connection.execute(
        """
        CREATE TEMP TABLE plr_display_name (
            plr_id TEXT NOT NULL,
            plr_name TEXT NOT NULL
        );

        CREATE TEMP TABLE plr_weather_population (
            plr_id TEXT NOT NULL,
            run_time_utc TIMESTAMPTZ NOT NULL,
            lead_time TEXT NOT NULL
        );

        CREATE TEMP TABLE plr_weather_context (
            plr_id TEXT NOT NULL,
            plr_name TEXT NOT NULL,
            run_time_utc TIMESTAMPTZ NOT NULL,
            lead_time TEXT NOT NULL,
            valid_time_berlin TIMESTAMP NOT NULL,
            temperature_c DOUBLE PRECISION NOT NULL,
            plr_temperature_median_c DOUBLE PRECISION NOT NULL,
            population_total BIGINT,
            population_65plus BIGINT,
            population_status TEXT NOT NULL
        );
        """
    )
    temporary_serving_sql = SERVING_SQL.replace("analytical.", "pg_temp.")
    temporary_serving_sql = temporary_serving_sql.replace(
        "CREATE OR REPLACE VIEW pg_temp.",
        "CREATE OR REPLACE TEMP VIEW ",
    )
    connection.execute(temporary_serving_sql)
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO pg_temp.plr_display_name VALUES (%s, %s)",
            [(plr_id, name) for plr_id, name, *_ in PLR_FIXTURES],
        )


def insert_forecast_leads(connection, run_time, lead_hours) -> None:
    source_rows = []
    serving_rows = []

    for lead_hour in lead_hours:
        lead_time = f"PT{lead_hour:03d}H00M"
        valid_time_berlin = (run_time + timedelta(hours=lead_hour + 2)).replace(
            tzinfo=None
        )
        for plr_id, name, total, older, status in PLR_FIXTURES:
            if plr_id == "01100101":
                temperature = 26.0 if lead_hour in {3, 8} else 20.0
                historical_median = 30.0
            else:
                temperature = 20.0 + lead_hour
                historical_median = 18.0

            source_rows.append((plr_id, run_time, lead_time))
            serving_rows.append(
                (
                    plr_id,
                    name,
                    run_time,
                    lead_time,
                    valid_time_berlin,
                    temperature,
                    historical_median,
                    total,
                    older,
                    status,
                )
            )

    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO pg_temp.plr_weather_population VALUES (%s, %s, %s)",
            source_rows,
        )
        cursor.executemany(
            """
            INSERT INTO pg_temp.plr_weather_context
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            serving_rows,
        )


def test_complete_horizon_preserves_signed_differences_ties_and_population():
    run_time = datetime(2026, 8, 24, 16, tzinfo=timezone.utc)

    with database_connection(
        application_name="capstone_forecast_horizon_sql_contract"
    ) as connection:
        install_temporary_serving_views(connection)
        insert_forecast_leads(connection, run_time, range(25))

        forecast_state = connection.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT plr_id),
                MIN(lead_hour),
                MAX(lead_hour),
                MAX(valid_time_berlin)
            FROM pg_temp.current_plr_temperature_forecast_25h
            """
        ).fetchone()
        first_summary = connection.execute(
            """
            SELECT
                plr_name,
                run_time_berlin,
                max_forecast_temperature_c,
                max_forecast_temperature_at_berlin,
                max_temperature_difference_c,
                max_temperature_difference_at_berlin,
                sum_temperature_difference_c,
                population_65plus,
                population_status
            FROM pg_temp.current_plr_temperature_summary_25h
            WHERE plr_id = '01100101'
            """
        ).fetchone()
        rejected_population = connection.execute(
            """
            SELECT population_total, population_65plus, population_status
            FROM pg_temp.current_plr_temperature_summary_25h
            WHERE plr_id = '01100102'
            """
        ).fetchone()

    assert forecast_state == (50, 2, 0, 24, datetime(2026, 8, 25, 18))
    assert first_summary == (
        "Stülerstraße",
        datetime(2026, 8, 24, 18),
        26.0,
        datetime(2026, 8, 24, 21),
        -4.0,
        datetime(2026, 8, 24, 21),
        -238.0,
        700,
        "available",
    )
    assert rejected_population == (None, None, "rejected_source_record")


def test_incomplete_newer_run_never_replaces_previous_complete_horizon():
    complete_run = datetime(2026, 8, 24, 16, tzinfo=timezone.utc)
    newer_run = complete_run + timedelta(hours=1)

    with database_connection(
        application_name="capstone_forecast_horizon_completeness_contract"
    ) as connection:
        install_temporary_serving_views(connection)
        insert_forecast_leads(connection, complete_run, range(25))
        insert_forecast_leads(connection, newer_run, range(24))

        before_completion = connection.execute(
            """
            SELECT MIN(run_time_berlin), COUNT(*)
            FROM pg_temp.current_plr_temperature_forecast_25h
            """
        ).fetchone()

        insert_forecast_leads(connection, newer_run, [24])
        after_completion = connection.execute(
            """
            SELECT MIN(run_time_berlin), COUNT(*)
            FROM pg_temp.current_plr_temperature_forecast_25h
            """
        ).fetchone()

    assert before_completion == (datetime(2026, 8, 24, 18), 50)
    assert after_completion == (datetime(2026, 8, 24, 19), 50)
