"""Classify existing tests without moving files or weakening their assertions."""

from __future__ import annotations

from pathlib import Path
import re

import pytest


DATABASE_TEST_FILES = {
    "test_analytical_weather_population_sql.py",
    "test_apparent_temperature_sql.py",
    "test_bootstrap_snapshot_sql.py",
    "test_database_boundary.py",
    "test_hostrada_backfill_sql.py",
    "test_hostrada_monthly_sql.py",
    "test_hostrada_reference_sql.py",
    "test_hostrada_spatial_sql.py",
    "test_icon_plr_area_bridge_quality.py",
    "test_population_sql_quality_gate.py",
    "test_spatial_bridge_sql.py",
    "test_spatial_sql_normalization.py",
    "test_weather_contract_sql.py",
    "test_weather_normalized_sql.py",
}

OPERATIONAL_ACCEPTANCE_FILES = {
    "test_analytical_weather_population_sql.py",
    "test_bootstrap_acceptance.py",
    "test_icon_plr_area_bridge_quality.py",
    "test_weather_normalized_sql.py",
}

HISTORICAL_REBUILD_FILES = {
    "test_hostrada_asset_definitions.py",
    "test_hostrada_backfill.py",
    "test_hostrada_backfill_sql.py",
    "test_hostrada_contract.py",
    "test_hostrada_download.py",
    "test_hostrada_monthly_assets.py",
    "test_hostrada_monthly_ingestion.py",
    "test_hostrada_monthly_sql.py",
    "test_hostrada_partitions.py",
    "test_hostrada_spatial_sql.py",
}

CONTRACT_TEST_FILES = {
    "test_apparent_temperature_sql.py",
    "test_bootstrap.py",
    "test_bootstrap_acceptance.py",
    "test_bootstrap_snapshot_sql.py",
    "test_distribution_manifest.py",
    "test_forecast_key.py",
    "test_hostrada_contract.py",
    "test_hostrada_reference.py",
    "test_hostrada_reference_sql.py",
    "test_hostrada_snapshot.py",
    "test_icon_grid_contract.py",
    "test_population_sql_quality_gate.py",
    "test_plr_display_names.py",
    "test_run_forecast.py",
    "test_spatial_bridge_sql.py",
    "test_static_snapshot.py",
    "test_weather_contract_sql.py",
    "test_weather_early_filtering.py",
    "test_weather_source_contract.py",
}


def pytest_ignore_collect(
    collection_path: Path,
    config: pytest.Config,
) -> bool | None:
    """Exclude optional rebuild modules before pytest attempts to import them."""
    if collection_path.name not in HISTORICAL_REBUILD_FILES:
        return None

    marker_expression = config.getoption("markexpr") or ""
    if re.search(r"\bnot\s+historical_rebuild\b", marker_expression):
        return True

    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config

    for item in items:
        filename = item.path.name
        is_database_test = filename in DATABASE_TEST_FILES

        # This one configuration-boundary test is genuinely database-free.
        if item.name == "test_database_settings_reports_missing_variables":
            is_database_test = False

        if is_database_test:
            item.add_marker(pytest.mark.integration)
        if filename in OPERATIONAL_ACCEPTANCE_FILES:
            item.add_marker(pytest.mark.acceptance)
        if filename in HISTORICAL_REBUILD_FILES:
            item.add_marker(pytest.mark.historical_rebuild)
        if (
            filename == "test_hostrada_reference.py"
            and item.name.startswith("test_hostrada_reference_build_")
        ):
            item.add_marker(pytest.mark.historical_rebuild)
        if filename in CONTRACT_TEST_FILES:
            item.add_marker(pytest.mark.contract)
