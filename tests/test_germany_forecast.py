"""Germany layer, M3: grid subset, SQL aggregation, checks 4-7, retention."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.dagster_pipeline.assets.germany_forecast import KEEP_COMPLETE_RUNS
from src.dagster_pipeline.checks import germany_forecast as checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORECAST_SQL = PROJECT_ROOT / "sql" / "germany" / "forecast_schema.sql"


def _code(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines()).upper()


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_forecast_ddl_is_additive_and_never_writes_berlin_objects() -> None:
    code = _code(FORECAST_SQL.read_text(encoding="utf-8"))
    for forbidden in ("DROP ", "TRUNCATE", "ALTER ", "UPDATE "):
        assert forbidden not in code
    for target in re.findall(r"(?:DELETE FROM|INSERT INTO) (\S+)", code):
        assert target.split(".")[-1].startswith("DE_"), target
    for name in re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)", code):
        assert name.split(".")[-1].startswith("DE_"), name
    for name in re.findall(r"CREATE OR REPLACE (?:VIEW|FUNCTION) (\S+?)[\s(]", code):
        assert "DE_" in name, name


@pytest.mark.contract
def test_forecast_reuses_berlin_units_and_formula() -> None:
    sql_text = FORECAST_SQL.read_text(encoding="utf-8")
    assert "normalized.calculate_apparent_temperature_shade_c(" in sql_text
    assert "stage.temperature_k - 273.15" in sql_text
    assert "SQRT(POWER(stage.wind_u_10m_ms, 2) + POWER(stage.wind_v_10m_ms, 2))" in sql_text
    assert "geometry(Polygon, 3035)" in sql_text


@pytest.mark.contract
def test_serving_views_contain_measurements_only() -> None:
    # D5: no thresholds, classes or exposure scores in the serving layer.
    code = _code(FORECAST_SQL.read_text(encoding="utf-8"))
    for word in ("THRESHOLD", "EXPOSURE", "RISK", "CLASS", "CATEGORY"):
        assert word not in code


@pytest.mark.contract
def test_check_thresholds_and_retention_are_the_documented_ones() -> None:
    assert checks.MIN_BRIDGE_COVERAGE == 0.999
    assert checks.TEMPERATURE_BOUNDS_C == (-50.0, 50.0)
    assert checks.APPARENT_TEMPERATURE_BOUNDS_C == (-70.0, 60.0)
    assert checks.BERLIN_CROSS_LAYER_TOLERANCE_C == 0.01
    assert KEEP_COMPLETE_RUNS == 2


def test_berlin_sensor_does_not_target_germany_jobs() -> None:
    from src.dagster_pipeline.definitions import defs

    sensor = defs.resolve_sensor_def("dwd_icon_d2_ruc_availability_sensor")
    assert sensor.job_name not in {
        "germany_static",
        "germany_forecast",
        "germany_forecast_publish",
    }
    for name in ("germany_static", "germany_forecast", "germany_forecast_publish"):
        assert defs.resolve_job_def(name) is not None


# ---------------------------------------------------------------------------
# Integration: fixture geography inside a rolled-back transaction.
# ---------------------------------------------------------------------------
FIXTURE = "fixture-m3"
GRID = "fixture_grid"


def _square(x: float, y: float, size: float = 1000.0) -> str:
    return (
        f"SRID=3035;POLYGON(({x} {y},{x + size} {y},{x + size} {y + size},"
        f"{x} {y + size},{x} {y}))"
    )


@pytest.mark.integration
def test_partition_aggregation_and_retention_keeps_two_runs() -> None:
    from src.database.connection import database_connection
    from src.database.load import copy_rows
    from src.germany.forecast import apply_forecast_schema

    x, y = 4_500_000.0, 3_200_000.0
    land = f"SRID=3035;MULTIPOLYGON((({x} {y},{x + 2000} {y},{x + 2000} {y + 1000},{x} {y + 1000},{x} {y})))"
    gem_a = f"SRID=3035;MULTIPOLYGON((({x} {y},{x + 1000} {y},{x + 1000} {y + 1000},{x} {y + 1000},{x} {y})))"
    gem_b = f"SRID=3035;MULTIPOLYGON((({x + 1000} {y},{x + 2000} {y},{x + 2000} {y + 1000},{x + 1000} {y + 1000},{x + 1000} {y})))"
    # Cell 1 covers A; cells 2 and 3 split B 50/50 (fractions of Gemeinde).
    bridge_rows = [(1, "990010000001", 1.0), (2, "990010000002", 0.5), (3, "990010000002", 0.5)]
    stage = {  # cell -> (T K, RH %, U, V)
        1: (293.15, 50.0, 3.0, 4.0),
        2: (283.15, 80.0, 0.0, 1.0),
        3: (288.15, 60.0, 1.0, 0.0),
    }
    base = datetime(2099, 1, 1, tzinfo=timezone.utc)

    with database_connection(application_name="lwr_germany_m3_test") as connection:
        with connection.transaction(force_rollback=True):
            apply_forecast_schema(connection)
            connection.execute(
                "INSERT INTO normalized.de_land VALUES (%s,'99','Fixland',%s,2e6,'f')",
                (FIXTURE, land),
            )
            connection.execute(
                "INSERT INTO normalized.de_kreis VALUES (%s,'99001','99','Fixkreis','Kreis',NULL,%s,2e6,'f')",
                (FIXTURE, land),
            )
            for ars, ags, geometry in (
                ("990010000001", "99001001", gem_a),
                ("990010000002", "99001002", gem_b),
            ):
                connection.execute(
                    "INSERT INTO normalized.de_gemeinde VALUES "
                    "(%s,%s,%s,'99001','99','G','Gemeinde',false,%s,1e6,'f')",
                    (FIXTURE, ars, ags, geometry),
                )
            for cell in (1, 2, 3):
                connection.execute(
                    "INSERT INTO normalized.de_icon_cell VALUES (%s,%s,%s,%s,1e6)",
                    (FIXTURE, GRID, cell, _square(x + 500 * (cell - 1), y)),
                )
            for cell, ars, fraction in bridge_rows:
                connection.execute(
                    "INSERT INTO normalized.de_icon_cell_gemeinde_bridge VALUES "
                    "(%s,%s,%s,%s,%s,1e6,%s)",
                    (FIXTURE, GRID, cell, ars, fraction * 1e6, fraction),
                )

            def load_run(run_time: datetime, leads: int) -> None:
                for hour in range(leads):
                    connection.execute(
                        "CREATE TEMP TABLE de_cell_stage (cell_index integer PRIMARY KEY, "
                        "temperature_k double precision, relative_humidity_percent "
                        "double precision, wind_u_10m_ms double precision, "
                        "wind_v_10m_ms double precision)"
                    )
                    copy_rows(
                        connection, schema="pg_temp", table="de_cell_stage",
                        columns=("cell_index", "temperature_k",
                                 "relative_humidity_percent", "wind_u_10m_ms",
                                 "wind_v_10m_ms"),
                        rows=[(c, v[0] + hour, *v[1:]) for c, v in stage.items()],
                    )
                    connection.execute(
                        "SELECT * FROM normalized.refresh_de_forecast_partition(%s,%s,%s,%s,%s)",
                        (run_time, f"PT{hour:03d}H00M", run_time + timedelta(hours=hour),
                         FIXTURE, GRID),
                    )
                    connection.execute("DROP TABLE pg_temp.de_cell_stage")

            runs = [base + timedelta(hours=h) for h in range(4)]
            for run_time in runs[:3]:
                load_run(run_time, 25)
            load_run(runs[3], 3)  # newer run still loading

            # Gemeinde values: A = cell 1, B = mean of cells 2 and 3. Expected
            # values are hand-computed from the formula
            # T + 0.33 e - 0.70 v - 4.00 with e = RH/100 * 6.105 * exp(17.27 T / (237.7 + T));
            # they equal normalized.calculate_apparent_temperature_shade_c(20, 50, 5) and
            # the mean of (10, 80, 1) and (15, 60, 1), queried on 2026-11-14.
            sql_values = dict(
                connection.execute(
                    "SELECT ars, apparent_temperature_shade_c FROM normalized.de_gemeinde_forecast "
                    "WHERE geography_version = %s AND run_time_utc = %s AND lead_time = 'PT000H00M'",
                    (FIXTURE, runs[0]),
                ).fetchall()
            )
            assert sql_values["990010000001"] == pytest.approx(16.348242312858147, abs=1e-12)
            assert sql_values["990010000002"] == pytest.approx(11.103011743711562, abs=1e-12)

            current = connection.execute(
                "SELECT run_time_utc FROM normalized.de_forecast_run_completeness "
                "WHERE geography_version = %s AND is_complete ORDER BY 1 DESC LIMIT 1",
                (FIXTURE,),
            ).fetchone()[0]
            assert current == runs[2]  # incomplete newer run is not current

            kept, cells, gemeinden = connection.execute(
                "SELECT * FROM normalized.prune_de_forecast(%s, 2)", (FIXTURE,)
            ).fetchone()
            assert kept == runs[1]
            assert gemeinden == 2 * 25
            assert cells == 3 * 25
            remaining = [
                r[0] for r in connection.execute(
                    "SELECT DISTINCT run_time_utc FROM normalized.de_gemeinde_forecast "
                    "WHERE geography_version = %s ORDER BY 1",
                    (FIXTURE,),
                )
            ]
            assert remaining == runs[1:]


@pytest.mark.integration
def test_installed_germany_forecast_passes_checks_4_to_7() -> None:
    for check in (
        checks.de_bridge_coverage,
        checks.de_forecast_completeness,
        checks.de_forecast_plausibility,
        checks.de_berlin_cross_layer_consistency,
    ):
        result = check.node_def.compute_fn.decorated_fn()
        assert result.passed, (check.check_key.name, result.metadata)
