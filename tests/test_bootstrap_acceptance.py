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


def test_current_serving_view_preserves_the_22_column_contract():
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

    assert row == (22,)
