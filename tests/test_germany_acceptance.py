"""Germany layer acceptance: run after `germany_static` and at least one
complete `python -m src.germany.run_forecast`. Reads the installed database
only; writes nothing.

    uv run --env-file .env python -m pytest -m acceptance tests/test_germany_acceptance.py
"""

import pytest

from src.database.connection import database_connection
from src.germany.contract import GEOGRAPHY_VERSION, VG250_CONTRACT


pytestmark = pytest.mark.acceptance

LEAD_TIMES = 25
LAENDER = VG250_CONTRACT.feature_counts["lan"][1]
KREISE = VG250_CONTRACT.feature_counts["krs"][1]
GEMEINDEN = VG250_CONTRACT.feature_counts["gem"][1]
# Roll-up identities: floating-point summation order only.
ROLLUP_TOLERANCE_C = 1e-9


def _one(sql: str, params: tuple = ()) -> tuple:
    with database_connection(application_name="lwr_germany_acceptance") as connection:
        return connection.execute(sql, params).fetchone()


def test_germany_group_is_registered_with_all_checks() -> None:
    from src.dagster_pipeline.definitions import defs

    graph = defs.resolve_asset_graph()
    germany_assets = {
        key.to_user_string()
        for key in graph.get_all_asset_keys()
        if graph.get(key).group_name == "germany"
    }
    assert {
        "raw/de_vg250",
        "raw/de_zensus2022_age",
        "normalized/de_hierarchy",
        "normalized/de_population",
        "normalized/de_icon_gemeinde_bridge",
        "normalized/de_gemeinde_forecast",
        "analytical/de_current_forecast_25h",
    } <= germany_assets
    check_names = {key.name for key in graph.asset_check_keys}
    assert {
        "de_key_coverage",
        "de_hierarchy_integrity",
        "de_source_totals",
        "de_bridge_coverage",
        "de_forecast_completeness",
        "de_forecast_plausibility",
        "de_berlin_cross_layer_consistency",
    } <= check_names


def test_all_germany_checks_pass_on_installed_data() -> None:
    from src.dagster_pipeline.checks.germany_forecast import GERMANY_FORECAST_CHECKS
    from src.dagster_pipeline.checks.germany_normalized import GERMANY_NORMALIZED_CHECKS

    for check in (*GERMANY_NORMALIZED_CHECKS, *GERMANY_FORECAST_CHECKS):
        result = check.node_def.compute_fn.decorated_fn()
        assert result.passed, (check.check_key.name, result.metadata)


def test_current_views_are_complete_at_all_four_levels() -> None:
    row = _one(
        """
        SELECT
            (SELECT COUNT(*) FROM analytical.de_current_gemeinde_forecast_25h),
            (SELECT COUNT(DISTINCT ars) FROM analytical.de_current_gemeinde_forecast_25h),
            (SELECT COUNT(*) FROM analytical.de_current_kreis_forecast_25h),
            (SELECT COUNT(*) FROM analytical.de_current_land_forecast_25h),
            (SELECT COUNT(*) FROM analytical.de_current_bund_forecast_25h),
            (SELECT COUNT(*) FROM analytical.de_current_gemeinde_summary),
            (SELECT COUNT(DISTINCT run_time_utc) FROM analytical.de_current_kreis_forecast_25h),
            (SELECT COUNT(DISTINCT lead_hour) FROM analytical.de_current_bund_forecast_25h)
        """
    )
    assert row == (
        GEMEINDEN * LEAD_TIMES,
        GEMEINDEN,
        KREISE * LEAD_TIMES,
        LAENDER * LEAD_TIMES,
        LEAD_TIMES,
        GEMEINDEN,
        1,
        LEAD_TIMES,
    )


def test_current_views_have_no_missing_measurements() -> None:
    row = _one(
        """
        SELECT
            (SELECT COUNT(*) FROM analytical.de_current_gemeinde_forecast_25h
             WHERE temperature_c IS NULL OR apparent_temperature_shade_c IS NULL),
            (SELECT COUNT(*) FROM analytical.de_current_kreis_forecast_25h
             WHERE temperature_area_weighted_c IS NULL
                OR temperature_population_weighted_c IS NULL
                OR apparent_temperature_shade_area_weighted_c IS NULL
                OR apparent_temperature_shade_population_weighted_c IS NULL),
            (SELECT COUNT(*) FROM analytical.de_current_gemeinde_summary
             WHERE max_temperature_c IS NULL OR max_temperature_at_berlin IS NULL
                OR max_apparent_temperature_shade_c IS NULL)
        """
    )
    assert row == (0, 0, 0)


def test_rollups_are_consistent_across_levels() -> None:
    # Area: Σ over Länder of (Land mean × Land area) / Σ area = Bund mean.
    # Population: same identity with Land population weights.
    row = _one(
        """
        WITH land AS (
            SELECT lead_hour,
                   SUM(temperature_area_weighted_c * area_m2) / SUM(area_m2) AS t_area,
                   SUM(temperature_population_weighted_c * population_total)
                       / SUM(population_total) AS t_pop
            FROM analytical.de_current_land_forecast_25h
            GROUP BY lead_hour
        )
        SELECT MAX(ABS(land.t_area - b.temperature_area_weighted_c)),
               MAX(ABS(land.t_pop - b.temperature_population_weighted_c)),
               (SELECT SUM(population_total) FROM analytical.de_current_land_forecast_25h
                WHERE lead_hour = 0)
        FROM land
        JOIN analytical.de_current_bund_forecast_25h AS b USING (lead_hour)
        """
    )
    area_difference, population_difference, population = row
    assert area_difference < ROLLUP_TOLERANCE_C
    assert population_difference < ROLLUP_TOLERANCE_C
    # Population weights are the census Gemeinde values (Σ Länder, check 3).
    assert population == 82_711_282


def test_retention_keeps_at_most_two_complete_runs() -> None:
    complete, incomplete = _one(
        """
        SELECT COUNT(*) FILTER (WHERE is_complete), COUNT(*) FILTER (WHERE NOT is_complete)
        FROM normalized.de_forecast_run_completeness
        WHERE geography_version = %s
        """,
        (GEOGRAPHY_VERSION,),
    )
    assert 1 <= complete <= 2
    assert incomplete <= 1
    runs_with_cells = _one(
        "SELECT COUNT(*) FROM normalized.de_forecast_run WHERE geography_version = %s",
        (GEOGRAPHY_VERSION,),
    )[0]
    assert runs_with_cells == complete + incomplete
