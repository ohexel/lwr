from src.database.connection import database_connection


def test_hostrada_source_manifest_records_verified_local_deletion():
    with database_connection(
        application_name="capstone_hostrada_source_retention_schema_test"
    ) as connection:
        result = connection.execute(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'raw'
              AND table_name = 'hostrada_month_source'
              AND column_name = 'source_deleted_at_utc'
            """
        ).fetchone()

    assert result == ("timestamp with time zone", "YES")
