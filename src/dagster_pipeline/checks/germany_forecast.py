"""Germany checks 4-7. Thresholds are documented in docs/germany-layer.md."""

import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_forecast import (
    ANALYTICAL_DE_CURRENT_FORECAST_KEY,
    NORMALIZED_DE_ICON_BRIDGE_KEY,
)
from src.germany.contract import GEOGRAPHY_VERSION
from src.germany.forecast import SOURCE_GRID_ID


# Check 4 (handover): Σ intersection / Gemeinde area >= 0.999.
MIN_BRIDGE_COVERAGE = 0.999
# Check 6: physical plausibility bounds, °C.
TEMPERATURE_BOUNDS_C = (-50.0, 50.0)
APPARENT_TEMPERATURE_BOUNDS_C = (-70.0, 60.0)
# Check 7: Berlin (VG250 Gemeinde) vs. area-weighted 542 PLRs, same cells.
# Observed max |difference| over the first three runs (2026-11-09 16-18 UTC,
# 75 lead times, both measures): 0.0014 °C. 0.01 °C leaves ~7x headroom for
# days with steeper gradients across the outline differences; warning only.
BERLIN_CROSS_LAYER_TOLERANCE_C = 0.01
EXPECTED_PLR_COUNT = 542
MAX_LISTED = 50


@dg.asset_check(asset=NORMALIZED_DE_ICON_BRIDGE_KEY, name="de_bridge_coverage")
def de_bridge_coverage() -> dg.AssetCheckResult:
    """Check 4: every Gemeinde is covered by ICON cells."""
    with database_connection(application_name="lwr_germany_check4") as connection:
        exceptions = connection.execute(
            "SELECT * FROM normalized.check_de_bridge_coverage(%s, %s, %s)",
            (GEOGRAPHY_VERSION, SOURCE_GRID_ID, MIN_BRIDGE_COVERAGE),
        ).fetchall()
        low, high, gemeinden = connection.execute(
            """
            SELECT MIN(s), MAX(s), COUNT(*) FROM (
                SELECT SUM(fraction_of_gemeinde) AS s
                FROM normalized.de_icon_cell_gemeinde_bridge
                WHERE geography_version = %s AND source_grid_id = %s
                GROUP BY ars
            ) AS coverage
            """,
            (GEOGRAPHY_VERSION, SOURCE_GRID_ID),
        ).fetchone()
    return dg.AssetCheckResult(
        passed=not exceptions and int(gemeinden) > 0,
        metadata={
            "min_coverage": MIN_BRIDGE_COVERAGE,
            "gemeinden_in_bridge": int(gemeinden),
            "observed_min_coverage": float(low or 0),
            "observed_max_coverage": float(high or 0),
            "exceptions": [f"{r[0]} {r[1]}: {r[2]:.6f}" for r in exceptions[:MAX_LISTED]],
            "exception_count": len(exceptions),
        },
        description="Σ cell intersection area / Gemeinde area is within [0.999, 1].",
    )


@dg.asset_check(asset=ANALYTICAL_DE_CURRENT_FORECAST_KEY, name="de_forecast_completeness")
def de_forecast_completeness() -> dg.AssetCheckResult:
    """Check 5: the served run has every Gemeinde × 25 lead times."""
    with database_connection(application_name="lwr_germany_check5") as connection:
        runs = connection.execute(
            """
            SELECT run_time_utc, lead_time_count, gemeinde_lead_row_count,
                   expected_row_count, is_complete
            FROM normalized.de_forecast_run_completeness
            WHERE geography_version = %s
            ORDER BY run_time_utc DESC
            """,
            (GEOGRAPHY_VERSION,),
        ).fetchall()
        current = connection.execute(
            "SELECT run_time_utc FROM analytical.de_current_run"
        ).fetchone()
    served = None if current is None else current[0]
    passed = served is not None and any(r[0] == served and r[4] for r in runs)
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "current_run_time_utc": None if served is None else served.isoformat(),
            "runs": [
                f"{r[0].isoformat()}: {r[1]}/25 leads, {r[2]}/{r[3]} rows, "
                f"{'complete' if r[4] else 'incomplete'}"
                for r in runs
            ],
        },
        description=(
            "The de_current_* views serve only a run with all Gemeinden × 25 "
            "lead times; newer incomplete runs are listed, never served."
        ),
    )


@dg.asset_check(asset=ANALYTICAL_DE_CURRENT_FORECAST_KEY, name="de_forecast_plausibility")
def de_forecast_plausibility() -> dg.AssetCheckResult:
    """Check 6: physical bounds and no NULLs, for Gemeinden and cells."""
    t_low, t_high = TEMPERATURE_BOUNDS_C
    a_low, a_high = APPARENT_TEMPERATURE_BOUNDS_C
    with database_connection(application_name="lwr_germany_check6") as connection:
        row = connection.execute(
            """
            WITH cur AS (SELECT * FROM analytical.de_current_run),
            g AS (
                SELECT f.* FROM normalized.de_gemeinde_forecast AS f, cur
                WHERE f.run_time_utc = cur.run_time_utc
                  AND f.geography_version = cur.geography_version
            ),
            c AS (
                SELECT w.* FROM normalized.de_icon_cell_weather AS w, cur
                WHERE w.run_time_utc = cur.run_time_utc
                  AND w.geography_version = cur.geography_version
            )
            SELECT
                (SELECT COUNT(*) FROM g),
                (SELECT COUNT(*) FROM g WHERE temperature_c IS NULL
                    OR apparent_temperature_shade_c IS NULL),
                (SELECT COUNT(*) FROM g WHERE temperature_c NOT BETWEEN %s AND %s
                    OR apparent_temperature_shade_c NOT BETWEEN %s AND %s),
                (SELECT COUNT(*) FROM c),
                (SELECT COUNT(*) FROM c WHERE temperature_c IS NULL
                    OR apparent_temperature_shade_c IS NULL),
                (SELECT COUNT(*) FROM c WHERE temperature_c NOT BETWEEN %s AND %s
                    OR apparent_temperature_shade_c NOT BETWEEN %s AND %s),
                (SELECT MIN(temperature_c) FROM g), (SELECT MAX(temperature_c) FROM g),
                (SELECT MIN(apparent_temperature_shade_c) FROM g),
                (SELECT MAX(apparent_temperature_shade_c) FROM g)
            """,
            (t_low, t_high, a_low, a_high, t_low, t_high, a_low, a_high),
        ).fetchone()
    g_rows, g_null, g_out, c_rows, c_null, c_out, t_min, t_max, a_min, a_max = row
    passed = g_rows > 0 and c_rows > 0 and not (g_null or g_out or c_null or c_out)
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "temperature_bounds_c": list(TEMPERATURE_BOUNDS_C),
            "apparent_temperature_bounds_c": list(APPARENT_TEMPERATURE_BOUNDS_C),
            "gemeinde_rows": int(g_rows),
            "gemeinde_null_rows": int(g_null),
            "gemeinde_out_of_bounds_rows": int(g_out),
            "cell_rows": int(c_rows),
            "cell_null_rows": int(c_null),
            "cell_out_of_bounds_rows": int(c_out),
            "observed_temperature_c": [t_min, t_max],
            "observed_apparent_temperature_c": [a_min, a_max],
        },
        description="Current run: no NULLs, values within physical bounds.",
    )


@dg.asset_check(
    asset=ANALYTICAL_DE_CURRENT_FORECAST_KEY,
    name="de_berlin_cross_layer_consistency",
)
def de_berlin_cross_layer_consistency() -> dg.AssetCheckResult:
    """Check 7: Berlin from the Germany bridge vs. its 542 PLRs (warning)."""
    with database_connection(application_name="lwr_germany_check7") as connection:
        current = connection.execute(
            "SELECT run_time_utc FROM analytical.de_current_run"
        ).fetchone()
        rows = [] if current is None else connection.execute(
            "SELECT * FROM normalized.de_berlin_cross_layer(%s, %s, %s)",
            (current[0], GEOGRAPHY_VERSION, SOURCE_GRID_ID),
        ).fetchall()

    def max_difference(index_a: int, index_b: int) -> float | None:
        values = [
            abs(r[index_a] - r[index_b])
            for r in rows
            if r[index_a] is not None and r[index_b] is not None
        ]
        return max(values) if values else None

    recomputed_t = max_difference(1, 2)
    recomputed_a = max_difference(4, 5)
    stored_t = max_difference(1, 3)
    stored_a = max_difference(4, 6)
    complete = len(rows) == 25 and all(r[7] == EXPECTED_PLR_COUNT for r in rows)
    within = (
        complete
        and recomputed_t is not None
        and max(recomputed_t, recomputed_a) <= BERLIN_CROSS_LAYER_TOLERANCE_C
    )
    stored_available = all(r[8] == EXPECTED_PLR_COUNT for r in rows) and bool(rows)

    return dg.AssetCheckResult(
        passed=within,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "tolerance_c": BERLIN_CROSS_LAYER_TOLERANCE_C,
            "lead_times_compared": len(rows),
            "max_abs_difference_temperature_c": recomputed_t,
            "max_abs_difference_apparent_c": recomputed_a,
            "stored_berlin_run_available": stored_available,
            "max_abs_difference_vs_stored_temperature_c": stored_t if stored_available else None,
            "max_abs_difference_vs_stored_apparent_c": stored_a if stored_available else None,
            "per_lead": [
                f"{r[0]}: DE {r[1]:.3f} / PLR {r[2]:.3f} °C; "
                f"apparent DE {r[4]:.3f} / PLR {r[5]:.3f} °C"
                for r in rows
                if None not in (r[1], r[2], r[4], r[5])
            ],
        },
        description=(
            "Berlin's value from the Germany bridge (VG250 outline) agrees with "
            "the area-weighted roll-up of the 542 PLRs computed from the same "
            "cell values through Berlin's bridge (LOR outline). Differences "
            "come only from the two outlines. A stored Berlin run for the same "
            "run time is compared when available. Warning severity."
        ),
    )


GERMANY_FORECAST_CHECKS = [
    de_bridge_coverage,
    de_forecast_completeness,
    de_forecast_plausibility,
    de_berlin_cross_layer_consistency,
]
