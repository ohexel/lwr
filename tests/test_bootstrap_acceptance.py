from src.bootstrap import query_static_state, validate_static_state
from src.database.connection import database_connection
from src.hostrada_snapshot import (
    SnapshotManifest,
    validate_snapshot,
    verify_installed_geography,
)


def test_operational_static_inputs_are_complete():
    manifest = SnapshotManifest.load()
    state = query_static_state(manifest)
    quality = validate_static_state(manifest)

    assert state.is_complete(manifest.plr_count), state.as_dict()
    assert all(quality.values()), quality


def test_imported_reference_matches_installed_geography_and_calendar():
    manifest = SnapshotManifest.load()
    geography = verify_installed_geography(manifest)
    quality = validate_snapshot(manifest)

    assert geography["plr_count"] == 542
    assert quality.plr_reference_count == 4_747_920
    assert quality.berlin_reference_count == 8_760
    assert quality.expected_observation_count == 271_559


def test_current_serving_view_preserves_the_23_column_contract():
    with database_connection(
        application_name="capstone_operational_serving_acceptance"
    ) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'analytical'
              AND table_name = 'current_plr_weather_context'
            """
        ).fetchone()

    assert row == (23,)


def test_current_forecast_serves_all_plrs_with_references_and_population_status():
    with database_connection(
        application_name="capstone_operational_forecast_acceptance"
    ) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT plr_id),
                COUNT(*) FILTER (WHERE plr_name IS NULL OR btrim(plr_name) = ''),
                COUNT(*) FILTER (
                    WHERE population_status = 'available'
                ),
                COUNT(*) FILTER (
                    WHERE population_status = 'rejected_source_record'
                ),
                COUNT(*) FILTER (
                    WHERE temperature_c IS NULL
                       OR apparent_temperature_shade_c IS NULL
                ),
                COUNT(*) FILTER (
                    WHERE population_status = 'available'
                      AND (
                          population_total IS NULL
                          OR population_65plus IS NULL
                      )
                ),
                COUNT(*) FILTER (
                    WHERE population_status = 'rejected_source_record'
                      AND (
                          population_total IS NOT NULL
                          OR population_65plus IS NOT NULL
                      )
                ),
                COUNT(*) FILTER (
                    WHERE (
                        EXTRACT(MONTH FROM valid_time_berlin),
                        EXTRACT(DAY FROM valid_time_berlin)
                    ) <> (2, 29)
                      AND (
                          plr_temperature_median_c IS NULL
                          OR plr_apparent_temperature_median_c IS NULL
                          OR berlin_temperature_median_c IS NULL
                          OR berlin_apparent_temperature_median_c IS NULL
                      )
                )
            FROM analytical.current_plr_weather_context
            """
        ).fetchone()

    assert row == (542, 542, 0, 540, 2, 0, 0, 0, 0), row
