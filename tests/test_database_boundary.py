import pytest
from psycopg.errors import UniqueViolation

from src.database.connection import (
    DatabaseSettings,
    database_connection,
    database_health,
)
from src.database.load import copy_rows

def test_database_settings_reports_missing_variables():
    with pytest.raises(
        RuntimeError,
        match="Missing required PostgreSQL environment variables",
    ):
        DatabaseSettings.from_env({})

def test_database_boundary_copy_and_transaction_rollback():
    with database_connection(
        application_name="capstone_phase_3_test"
    ) as connection:
        health = database_health(connection)

        assert health["database"]
        assert health["postgres_version"]
        assert health["postgis_version"]

        connection.execute(
            '''
            CREATE TEMP TABLE phase_3_copy_test (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            )
            ON COMMIT DROP
            '''
        )

        result = copy_rows(
            connection,
            schema="pg_temp",
            table="phase_3_copy_test",
            columns=("id", "label"),
            rows=[(1, "alpha"), (2, "beta")],
        )

        assert result.row_count == 2
        rows = connection.execute(
            '''
            SELECT id, label
            FROM phase_3_copy_test
            ORDER BY id
            '''
        ).fetchall()
        assert rows == [(1, "alpha"), (2, "beta")]

        connection.execute(
            '''
            CREATE TEMP TABLE phase_3_rollback_test (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            )
            ON COMMIT DROP
            '''
        )

        with pytest.raises(UniqueViolation):
            with connection.transaction():
                copy_rows(
                    connection,
                    schema="pg_temp",
                    table="phase_3_rollback_test",
                    columns=("id", "label"),
                    rows=[
                        (1, "first"),
                        (1, "duplicate"),
                    ],
                )

        count = connection.execute(
            "SELECT COUNT(*) FROM phase_3_rollback_test"
        ).fetchone()[0]
        assert count == 0
