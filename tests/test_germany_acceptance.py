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


def _one(sql: str, params: tuple | dict = ()) -> tuple:
    with database_connection(application_name="lwr_germany_acceptance") as connection:
        return connection.execute(sql, params).fetchone()


def _all(sql: str, params: tuple | dict = ()) -> list[tuple]:
    with database_connection(application_name="lwr_germany_acceptance") as connection:
        return connection.execute(sql, params).fetchall()


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
        "raw/de_zensus2022_population",
        "normalized/de_hierarchy",
        "normalized/de_population",
        "normalized/de_icon_gemeinde_bridge",
        "normalized/de_gemeinde_forecast",
        "analytical/de_current_forecast_25h",
        "raw/de_ba_gemeindedaten",
        "normalized/de_indicator_value",
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
        "de_ba_source_contract",
        "de_ba_column_identities",
        "de_ba_key_coverage",
        "de_ba_source_totals",
    } <= check_names


def test_all_germany_checks_pass_on_installed_data() -> None:
    from src.dagster_pipeline.checks.germany_employment import GERMANY_EMPLOYMENT_CHECKS
    from src.dagster_pipeline.checks.germany_forecast import GERMANY_FORECAST_CHECKS
    from src.dagster_pipeline.checks.germany_normalized import GERMANY_NORMALIZED_CHECKS

    for check in (*GERMANY_NORMALIZED_CHECKS, *GERMANY_FORECAST_CHECKS, *GERMANY_EMPLOYMENT_CHECKS):
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
                OR max_apparent_temperature_shade_c IS NULL),
            (SELECT COUNT(*) FROM analytical.de_current_gemeinde_summary
             WHERE min_temperature_c IS NULL OR min_temperature_at_berlin IS NULL
                OR min_apparent_temperature_shade_c IS NULL
                OR min_apparent_temperature_shade_at_berlin IS NULL)
        """
    )
    assert row == (0, 0, 0, 0)


# (forecast view, summary view, region key, measure) for every served extreme.
WEIGHTED = (
    "temperature_area_weighted",
    "temperature_population_weighted",
    "apparent_temperature_shade_area_weighted",
    "apparent_temperature_shade_population_weighted",
)
SUMMARIES = [
    ("de_current_gemeinde_forecast_25h", "de_current_gemeinde_summary", "ars", measure)
    for measure in ("temperature", "apparent_temperature_shade")
] + [
    (f"de_current_{level}_forecast_25h", f"de_current_{level}_summary", key, measure)
    for level, key in (("kreis", "kreis_key"), ("land", "land_key"), ("bund", "run_time_utc"))
    for measure in WEIGHTED
]


@pytest.mark.parametrize(("forecast", "summary", "key", "measure"), SUMMARIES)
def test_summary_extremes_follow_the_documented_rule(
    forecast: str, summary: str, key: str, measure: str
) -> None:
    # Highest / lowest value of the 25 lead hours; on a tie the earliest time.
    # Recomputed with plain MAX/MIN here, independent of the views' ranking.
    # Kreis and Land values are sums evaluated afresh per query, so values may
    # differ in summation order (measured up to 7e-15 °C); times must match.
    mismatches, regions = _one(
        f"""
        WITH f AS (
            SELECT {key} AS region, valid_time_berlin, {measure}_c AS v
            FROM analytical.{forecast}
        ),
        e AS (SELECT region, MAX(v) AS hi, MIN(v) AS lo FROM f GROUP BY region),
        expected AS (
            SELECT e.region, e.hi, e.lo,
                   MIN(f.valid_time_berlin) FILTER (WHERE f.v = e.hi) AS hi_at,
                   MIN(f.valid_time_berlin) FILTER (WHERE f.v = e.lo) AS lo_at
            FROM e JOIN f USING (region)
            GROUP BY e.region, e.hi, e.lo
        )
        SELECT COUNT(*) FILTER (
                   WHERE COALESCE(ABS(s.max_{measure}_c - x.hi) > %(tolerance)s,
                                  s.max_{measure}_c IS DISTINCT FROM x.hi)
                      OR COALESCE(ABS(s.min_{measure}_c - x.lo) > %(tolerance)s,
                                  s.min_{measure}_c IS DISTINCT FROM x.lo)
                      OR s.max_{measure}_at_berlin IS DISTINCT FROM x.hi_at
                      OR s.min_{measure}_at_berlin IS DISTINCT FROM x.lo_at),
               COUNT(*)
        FROM expected AS x
        JOIN analytical.{summary} AS s ON s.{key} = x.region
        """,
        {"tolerance": ROLLUP_TOLERANCE_C},
    )
    assert regions > 0
    assert mismatches == 0


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


def test_map_values_export_is_complete_for_the_current_run() -> None:
    from src.export_germany_dashboard import collect_values

    with database_connection(application_name="lwr_germany_acceptance") as connection:
        values = collect_values(connection)  # raises on any gap
        current = connection.execute(
            "SELECT run_time_utc FROM analytical.de_current_run"
        ).fetchone()[0]
    assert values["run_time_utc"] == current.strftime("%Y-%m-%dT%H:%MZ")
    assert values["tiles"] == f"{GEOGRAPHY_VERSION}.pmtiles"
    assert len(values["valid_times_berlin"]) == LEAD_TIMES
    assert (len(values["lan"]), len(values["krs"]), len(values["gem"])) == (
        LAENDER, KREISE, GEMEINDEN,
    )
    assert values["bund"]["population_total"] == 82_711_282
    low, high = values["apparent_range_c"]
    assert all(
        low <= r["max_apparent_temperature_shade_c"] <= high for r in values["gem"].values()
    )
    # Minima for the Höchst-/Tiefstwerte switch: present everywhere, never
    # above the maximum of the same measure.
    for level in ("gem", "krs", "lan"):
        for record in values[level].values():
            minima = [k for k in record if k.startswith("min_") and k.endswith("_c")]
            assert len(minima) == (2 if level == "gem" else 4)
            for k in minima:
                assert ("max_" + k[4:]) in record and (k.removesuffix("_c") + "_lead_hour") in record
                if record[k] is not None:
                    assert record[k] <= record["max_" + k[4:]]
    bund = values["bund"]
    for weighting in ("area", "population"):
        key = f"apparent_temperature_shade_{weighting}_weighted_c"
        assert bund[f"min_{key}"] <= bund[f"max_{key}"]


# ---------------------------------------------------------------------------
# Second theme: employment (BA Gemeindedaten), after python -m src.germany.run_employment
# ---------------------------------------------------------------------------
def test_employment_view_covers_every_region_with_an_explicit_status() -> None:
    from src.germany.contract import BA_GEMEINDEDATEN_CONTRACT

    rows = dict(
        ((level, status), count)
        for level, status, count in _all(
            """
            SELECT level, jobs_ratio_status, COUNT(*)
            FROM analytical.de_current_employment GROUP BY 1, 2
            """
        )
    )
    reference_date = _one(
        "SELECT DISTINCT reference_date FROM analytical.de_current_employment"
    )[0]
    known = BA_GEMEINDEDATEN_CONTRACT.vintage(reference_date)
    assert sum(c for (level, _), c in rows.items() if level == "gem") == GEMEINDEN
    assert rows[("krs", "value")] == KREISE
    assert rows[("lan", "value")] == LAENDER
    assert rows[("sta", "value")] == 1
    if known is not None:
        # Not reported: the unincorporated areas without employees (204) and
        # the Gemeinden merged after the geography version.
        assert rows[("gem", "not_reported")] == 204 + len(known.geography_without_source)
    # The ratio is exactly the quotient of the two published values.
    mismatches = _one(
        """
        SELECT COUNT(*) FROM analytical.de_current_employment
        WHERE jobs_ratio_status = 'value'
          AND jobs_ratio IS DISTINCT FROM employees_workplace / employees_residence
        """
    )[0]
    assert mismatches == 0


def test_employment_export_is_complete() -> None:
    from src.export_germany_dashboard import collect_employment

    with database_connection(application_name="lwr_germany_acceptance") as connection:
        employment = collect_employment(connection)
    assert employment is not None
    assert (len(employment["lan"]), len(employment["krs"]), len(employment["gem"])) == (
        LAENDER, KREISE, GEMEINDEN,
    )
    bund = employment["bund"]
    assert bund["jobs_ratio_status"] == "value"
    assert bund["employees_workplace"] >= sum(
        r["employees_workplace"] for r in employment["lan"].values()
    )
