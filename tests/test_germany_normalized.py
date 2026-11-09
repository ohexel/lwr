"""Germany layer, M2: normalized hierarchy, population and checks 1-2."""

import re
from pathlib import Path

import pytest

from src.germany.contract import GEOGRAPHY_VERSION, VG250_CONTRACT
from src.germany.normalize import zensus_source_sha256s


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_SQL = PROJECT_ROOT / "sql" / "germany" / "normalized_schema.sql"


def _code(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


@pytest.mark.contract
def test_normalized_ddl_is_additive_and_prefixed() -> None:
    code = _code(NORMALIZED_SQL.read_text(encoding="utf-8")).upper()
    for forbidden in ("DROP ", "TRUNCATE", "ALTER "):
        assert forbidden not in code
    # Deletes only ever target Germany relations.
    for target in re.findall(r"DELETE FROM (\S+)", code):
        assert target.split(".")[-1].startswith("DE_")
    for name in re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)", code):
        assert name.split(".")[-1].startswith("DE_")
    for name in re.findall(r"CREATE OR REPLACE FUNCTION (\S+)\(", code):
        assert "_DE_" in name or name.split(".")[-1].startswith("DE_")


@pytest.mark.contract
def test_normalized_geometry_is_equal_area_3035() -> None:
    sql_text = NORMALIZED_SQL.read_text(encoding="utf-8")
    assert "geometry(MultiPolygon, 3035)" in sql_text
    assert "25833" not in sql_text  # Berlin's CRS stays Berlin's.


@pytest.mark.contract
def test_population_null_is_never_zero() -> None:
    sql_text = NORMALIZED_SQL.read_text(encoding="utf-8")
    # Missing records keep NULL population with an explicit status.
    assert "'no_census_record_unincorporated'" in sql_text
    assert "COALESCE(census.population_total" not in sql_text
    # '-' ("Genau Null oder auf Null geändert") is the only sign mapped to 0.
    assert "WHEN raw_row.value = '-' THEN 0" in sql_text


def test_geography_version_names_the_vg250_vintage() -> None:
    assert GEOGRAPHY_VERSION == f"vg250-{VG250_CONTRACT.vintage.isoformat()}"
    assert len(zensus_source_sha256s()) == 3


# ---------------------------------------------------------------------------
# Integration: synthetic sources inside a rolled-back transaction.
# ---------------------------------------------------------------------------
FIXTURE_VG250 = "fixture_de_vg250_m2"
FIXTURE_ZENSUS = "fixture_de_zensus_m2"
FIXTURE_VERSION = "fixture-m2"


def _square(x0: float, y0: float, size: float = 1000.0) -> str:
    x1, y1 = x0 + size, y0 + size
    return (
        f"SRID=25832;MULTIPOLYGON((({x0} {y0},{x1} {y0},{x1} {y1},"
        f"{x0} {y1},{x0} {y0})))"
    )


def _vg250(level, ars, name, bez, geometry, ags=None):
    return (
        level, 4, ars, ags, name, bez, ars[:2], ars[2:3] or None,
        ars[3:5] or None, geometry, "2022-12-31", f"vg250_{level}",
        "fixture.gpkg", FIXTURE_VG250, "fixture", "fixture", "fixture",
    )


def _zensus(variable, key, value, label="x"):
    return (
        "1000A-1003", variable, "1000A", "fixture", "STAG", "Stichtag",
        "2022-05-15", variable, "fixture", key, label, None, None, None,
        None, value, "Anzahl", "PRS", "Personen", "fixture.csv",
        FIXTURE_ZENSUS, "fixture", "fixture", "fixture",
    )


@pytest.mark.integration
def test_refresh_and_checks_classify_every_case() -> None:
    from src.database.connection import database_connection
    from src.database.load import copy_rows
    from src.germany.normalize import apply_normalized_schema

    x, y = 500_000.0, 5_800_000.0
    vg250_rows = [
        _vg250("lan", "99", "Fixland", "Land", _square(x, y, 10_000)),
        _vg250("krs", "99001", "Fixkreis", "Kreis", _square(x, y, 4000)),
        _vg250("krs", "99002", "Leerkreis", "Kreis", _square(x + 5000, y)),
        # census value / '-' / unincorporated without record /
        # populated without record / rejected value
        _vg250("gem", "990010000001", "Eins", "Gemeinde", _square(x, y), "99001001"),
        _vg250("gem", "990010000002", "Zwei", "Gemeinde", _square(x + 1000, y), "99001002"),
        _vg250("gem", "990019501501", "Forst", "Gemeindefreies Gebiet", _square(x + 2000, y), "99001501"),
        _vg250("gem", "990010000004", "Vier", "Gemeinde", _square(x + 3000, y), "99001004"),
        _vg250("gem", "990010000006", "Sechs", "Gemeinde", _square(x, y + 1000), "99001006"),
    ]
    zensus_rows = [
        _zensus("GEODL1", "DG", "999"),
        _zensus("GEOBL1", "99", "500"),
        _zensus("GEOLK4", "99001", "500"),
        _zensus("GEOLK4", "99002", "0"),
        _zensus("GEOGM4", "990010000001", "100"),
        _zensus("GEOGM4", "990010000002", "-"),
        _zensus("GEOGM4", "990010000006", "x"),
        _zensus("GEOGM4", "990010000099", "7", "Phantom"),
    ]

    with database_connection(application_name="lwr_germany_m2_test") as connection:
        with connection.transaction(force_rollback=True):
            apply_normalized_schema(connection)
            copy_rows(
                connection, schema="raw", table="de_vg250",
                columns=(
                    "level", "gf", "ars", "ags", "gen", "bez", "sn_l", "sn_r",
                    "sn_k", "geometry_source", "vintage", "source_layer",
                    "source_path", "source_sha256", "source_url", "publisher",
                    "license",
                ),
                rows=vg250_rows,
            )
            copy_rows(
                connection, schema="raw", table="de_zensus2022_age",
                columns=(
                    "table_code", "requested_regional_variable",
                    "statistics_code", "statistics_label", "time_code",
                    "time_label", "time_value", "variable_1_code",
                    "variable_1_label", "variable_1_attribute_code",
                    "variable_1_attribute_label", "variable_2_code",
                    "variable_2_label", "variable_2_attribute_code",
                    "variable_2_attribute_label", "value", "value_unit",
                    "value_variable_code", "value_variable_label",
                    "source_path", "source_sha256", "source_url", "publisher",
                    "license",
                ),
                rows=zensus_rows,
            )

            hierarchy = connection.execute(
                "SELECT * FROM normalized.refresh_de_hierarchy(%s, %s)",
                (FIXTURE_VG250, FIXTURE_VERSION),
            ).fetchone()
            assert hierarchy == (1, 2, 5, 1)

            connection.execute(
                "SELECT * FROM normalized.refresh_de_population(%s, %s, %s)",
                (FIXTURE_VERSION, [FIXTURE_ZENSUS], "2022-05-15"),
            ).fetchone()

            population = dict(
                (ars, (total, status))
                for ars, total, status in connection.execute(
                    """
                    SELECT ars, population_total, population_status
                    FROM normalized.de_gemeinde_population
                    WHERE geography_version = %s
                    """,
                    (FIXTURE_VERSION,),
                )
            )
            assert population == {
                "990010000001": (100, "census_value"),
                "990010000002": (0, "census_zero_or_set_to_zero"),
                "990019501501": (None, "no_census_record_unincorporated"),
                "990010000004": (None, "no_census_record"),
                "990010000006": (None, "rejected_source_value"),
            }

            coverage = {
                (issue, key, blocking)
                for _, issue, key, _, blocking in connection.execute(
                    "SELECT * FROM normalized.check_de_key_coverage(%s, %s)",
                    (FIXTURE_VERSION, [FIXTURE_ZENSUS]),
                )
            }
            assert coverage == {
                ("unincorporated_area_without_census_record", "990019501501", False),
                ("geometry_without_census_record", "990010000004", True),
                ("census_record_without_geometry", "990010000099", True),
                ("rejected_census_value", "990010000006", True),
            }

            hierarchy_issues = set(
                connection.execute(
                    "SELECT issue, region_key FROM normalized.check_de_hierarchy(%s)",
                    (FIXTURE_VERSION,),
                ).fetchall()
            )
            assert hierarchy_issues == {("kreis_without_gemeinde", "99002")}


@pytest.mark.integration
def test_installed_germany_hierarchy_passes_checks_1_and_2() -> None:
    from src.dagster_pipeline.checks.germany_normalized import (
        de_hierarchy_integrity,
        de_key_coverage,
    )

    for check in (de_key_coverage, de_hierarchy_integrity):
        result = check.node_def.compute_fn.decorated_fn()
        assert result.passed, result.metadata
