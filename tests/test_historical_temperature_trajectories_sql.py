"""Exercise real historical extraction against compact disposable fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from src.database.connection import database_connection


HISTORICAL_SQL = (
    Path(__file__).resolve().parent.parent
    / "sql"
    / "plr_temperature_history_25h.sql"
).read_text(encoding="utf-8")
BERLIN = ZoneInfo("Europe/Berlin")
RUN_TIME_UTC = datetime(2026, 8, 31, 21, tzinfo=timezone.utc)
PLRS = (
    ("01100101", "Stülerstraße"),
    ("01100102", "Großer Tiergarten"),
)


def install_temporary_historical_objects(connection):
    connection.execute(
        """
        CREATE TEMP TABLE hostrada_plr_hourly (
            source_month_utc DATE NOT NULL,
            valid_time_utc TIMESTAMPTZ NOT NULL,
            plr_id TEXT NOT NULL,
            geography_version TEXT NOT NULL,
            temperature_c DOUBLE PRECISION NOT NULL,
            apparent_temperature_shade_c DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (source_month_utc, valid_time_utc, plr_id)
        );

        CREATE TEMP TABLE current_plr_temperature_forecast_25h (
            plr_id TEXT NOT NULL,
            plr_name TEXT NOT NULL,
            run_time_berlin TIMESTAMP NOT NULL,
            lead_hour INTEGER NOT NULL,
            valid_time_berlin TIMESTAMP NOT NULL,
            forecast_temperature_c DOUBLE PRECISION NOT NULL,
            forecast_apparent_temperature_c DOUBLE PRECISION NOT NULL,
            historical_temperature_median_c DOUBLE PRECISION NOT NULL,
            historical_apparent_temperature_median_c DOUBLE PRECISION NOT NULL
        );
        """
    )

    temporary_sql = HISTORICAL_SQL.replace("analytical.", "pg_temp.")
    temporary_sql = temporary_sql.replace(
        "CREATE OR REPLACE VIEW pg_temp.",
        "CREATE OR REPLACE TEMP VIEW ",
    )
    connection.execute(temporary_sql)


def populate_horizon(connection):
    local_run = RUN_TIME_UTC.astimezone(BERLIN).replace(tzinfo=None)
    forecast_rows = []
    historical_rows = []

    for lead in range(25):
        forecast_local = local_run + timedelta(hours=lead)
        for plr_id, name in PLRS:
            forecast_rows.append(
                (
                    plr_id,
                    name,
                    local_run,
                    lead,
                    forecast_local,
                    28.0,
                    30.0,
                    21.0,
                    22.0,
                )
            )

            for year in range(1995, 2027):
                historical_local = forecast_local.replace(year=year)
                historical_utc = historical_local.replace(tzinfo=BERLIN).astimezone(
                    timezone.utc
                )
                historical_rows.append(
                    (
                        historical_utc.date().replace(day=1),
                        historical_utc,
                        plr_id,
                        "2023-01-01",
                        float(year) + lead / 100,
                        float(year) + lead / 100 + 2,
                    )
                )

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO pg_temp.current_plr_temperature_forecast_25h
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            forecast_rows,
        )
        cursor.executemany(
            "INSERT INTO pg_temp.hostrada_plr_hourly VALUES (%s, %s, %s, %s, %s, %s)",
            historical_rows,
        )


def test_history_extracts_31_years_across_a_berlin_month_boundary():
    with database_connection(
        application_name="capstone_historical_trajectory_sql_contract"
    ) as connection:
        install_temporary_historical_objects(connection)
        populate_horizon(connection)

        result = connection.execute(
            "SELECT * FROM pg_temp.refresh_plr_temperature_history_25h(%s)",
            (RUN_TIME_UTC,),
        ).fetchone()
        plotting = connection.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT plr_id),
                COUNT(DISTINCT historical_year),
                MIN(historical_year),
                MAX(historical_year),
                MIN(lead_hour),
                MAX(lead_hour)
            FROM pg_temp.current_plr_temperature_history_25h
            """
        ).fetchone()
        boundary = connection.execute(
            """
            SELECT
                plr_name,
                valid_time_berlin,
                historical_valid_time_berlin,
                historical_temperature_c,
                historical_apparent_temperature_c,
                forecast_temperature_c,
                forecast_apparent_temperature_c
            FROM pg_temp.current_plr_temperature_history_25h
            WHERE plr_id = '01100101'
              AND historical_year = 2000
              AND lead_hour = 1
            """
        ).fetchone()
        repeated = connection.execute(
            "SELECT * FROM pg_temp.refresh_plr_temperature_history_25h(%s)",
            (RUN_TIME_UTC,),
        ).fetchone()

    assert result == (2, 31, 25, 1_550, False)
    assert plotting == (1_550, 2, 31, 1995, 2025, 0, 24)
    assert boundary == (
        "Stülerstraße",
        datetime(2026, 9, 1, 0),
        datetime(2000, 9, 1, 0),
        2000.01,
        2002.01,
        28.0,
        30.0,
    )
    assert repeated == (2, 31, 25, 1_550, True)


def test_failed_refresh_keeps_previous_historical_trajectories_transactionally():
    with database_connection(
        application_name="capstone_historical_trajectory_atomicity"
    ) as connection:
        install_temporary_historical_objects(connection)
        populate_horizon(connection)
        connection.execute(
            "SELECT * FROM pg_temp.refresh_plr_temperature_history_25h(%s)",
            (RUN_TIME_UTC,),
        )

        newer_run = RUN_TIME_UTC + timedelta(hours=1)
        connection.execute(
            """
            UPDATE pg_temp.current_plr_temperature_forecast_25h
            SET
                run_time_berlin = run_time_berlin + INTERVAL '1 hour',
                valid_time_berlin = valid_time_berlin + INTERVAL '1 hour'
            """
        )
        connection.execute("SAVEPOINT before_failed_history_refresh")

        with pytest.raises(psycopg.Error, match="expected 1550"):
            connection.execute(
                "SELECT * FROM pg_temp.refresh_plr_temperature_history_25h(%s)",
                (newer_run,),
            )

        connection.execute("ROLLBACK TO SAVEPOINT before_failed_history_refresh")
        retained = connection.execute(
            """
            SELECT MIN(run_time_utc), COUNT(*)
            FROM pg_temp.plr_temperature_history_25h
            """
        ).fetchone()

    assert retained == (RUN_TIME_UTC, 1_550)


def test_empty_historical_source_explains_compact_snapshot_limitation():
    with database_connection(
        application_name="capstone_historical_trajectory_missing_source"
    ) as connection:
        install_temporary_historical_objects(connection)
        populate_horizon(connection)
        connection.execute("DELETE FROM pg_temp.hostrada_plr_hourly")

        with pytest.raises(psycopg.Error, match="compact reference snapshot"):
            connection.execute(
                "SELECT * FROM pg_temp.refresh_plr_temperature_history_25h(%s)",
                (RUN_TIME_UTC,),
            )
