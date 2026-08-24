from datetime import date

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT


def test_hostrada_hourly_tables_retain_temperature_outputs_only():
    with database_connection(
        application_name="capstone_hostrada_monthly_schema_test"
    ) as connection:
        rows = connection.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'analytical'
              AND table_name IN (
                    'hostrada_plr_hourly',
                    'hostrada_berlin_hourly'
              )
            """
        ).fetchall()

    columns = {
        table_name: {
            column_name
            for observed_table, column_name in rows
            if observed_table == table_name
        }
        for table_name in ("hostrada_plr_hourly", "hostrada_berlin_hourly")
    }

    for retained_columns in columns.values():
        assert {"temperature_c", "apparent_temperature_shade_c"}.issubset(
            retained_columns
        )
        assert "relative_humidity_percent" not in retained_columns
        assert "wind_speed_10m_ms" not in retained_columns


def test_hostrada_month_quality_rejects_an_unmaterialized_month():
    with database_connection(
        application_name="capstone_hostrada_monthly_quality_test"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT
                passed,
                source_file_count,
                expected_hour_count,
                incomplete_plr_hour_count,
                missing_berlin_hour_count
            FROM analytical.check_hostrada_month_quality(
                %s::DATE,
                %s::TEXT,
                %s::TEXT
            )
            """,
            (
                date(9998, 1, 1),
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    assert result == (False, 0, 744, 744, 744)
