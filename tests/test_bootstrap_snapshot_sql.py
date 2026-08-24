from src.database.connection import database_connection


def test_snapshot_validator_has_no_historical_source_dependencies():
    with database_connection(
        application_name="capstone_snapshot_function_contract"
    ) as connection:
        row = connection.execute(
            """
            SELECT pg_get_functiondef(
                'analytical.check_hostrada_reference_snapshot(text,integer)'
                    ::REGPROCEDURE
            )
            """
        ).fetchone()

    assert row is not None
    function_definition = str(row[0])
    assert "raw.hostrada_month_source" not in function_definition
    assert "analytical.hostrada_plr_hourly AS" not in function_definition
    assert "analytical.hostrada_berlin_hourly AS" not in function_definition
    assert "analytical.hostrada_reference_expected_hours" in function_definition


def test_snapshot_validator_rejects_an_uninstalled_geography():
    with database_connection(
        application_name="capstone_snapshot_missing_geography"
    ) as connection:
        row = connection.execute(
            """
            SELECT
                passed,
                expected_plr_count,
                installed_plr_count,
                expected_calendar_hour_count,
                expected_observation_count
            FROM analytical.check_hostrada_reference_snapshot(
                '__missing_snapshot_geography__',
                542
            )
            """
        ).fetchone()

    assert row == (False, 542, 0, 8760, 271559)
