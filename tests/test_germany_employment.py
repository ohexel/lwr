"""Germany layer, second theme: BA Gemeindedaten (employment per Gemeinde).

A small synthetic workbook with the BA layout drives the parser tests and,
inside a rolled-back transaction, the raw load, the refresh onto a fixture
hierarchy and the check functions. Its numbers are consistent by
construction: Gemeinden sum to their Kreis (one Gemeinde has suppressed
cells), Kreise to the Land, and the Land plus the unassigned row to Germany.
"""

import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from src.germany.ba_gemeindedaten import (
    BaContractError,
    RAW_COLUMNS,
    SCHEMA_PATH,
    raw_rows,
    read_workbook,
)
from src.germany.contract import BA_GEMEINDEDATEN_CONTRACT


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DATE = date(1999, 6, 30)  # no real vintage uses it
FIXTURE = "fixture-ba"
# The synthetic workbook has one Land; everything else is the real contract.
CONTRACT = replace(BA_GEMEINDEDATEN_CONTRACT, land_count=1)

# 13 BA columns: Wohnort, Männer, Frauen, Deutsche, Ausländer, u. 20, 20-25,
# 55+, Arbeitsort, Wohnort = Arbeitsort, Einpendler, Auspendler, Betriebe.
GEM_A = (100, 60, 40, 90, 10, 5, 10, 30, 150, 40, 110, 60, 12)
GEM_B = (50, 30, 20, 45, 5, "*", 6, 15, "*", "*", "*", 40, "*")  # hidden: u20 4, work 20, est 2
GEM_C = (0,) * 13  # in the BA file only (a merger after the geography version)
KREIS = (150, 90, 60, 135, 15, 9, 16, 45, 170, 100, 70, 50, 14)
STADT = (200, 110, 90, 180, 20, 8, 14, 60, 300, 120, 180, 80, 25)  # kreisfreie Stadt
LAND = (350, 200, 150, 315, 35, 17, 30, 105, 470, 300, 170, 50, 39)
UNASSIGNED = (0, 0, 0, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0)
GERMANY = (350, 200, 150, 315, 35, 17, 30, 105, 475, 0, 0, 0, 39)


def _header(sheet) -> None:
    sheet.cell(row=2, column=1, value="Gemeindedaten aus der Beschäftigungsstatistik")
    sheet.cell(row=5, column=1, value="Schl.\nNr.")
    sheet.cell(
        row=5,
        column=3,
        value=(
            "Sozialversicherungspflichtig Beschäftigte am 30.06.1999 mit "
            "Wohnort / Arbeitsort in der vg. Gemeinde"
        ),
    )
    sheet.cell(row=5, column=15, value="Zahl \nder \nBetriebe")
    sheet.cell(row=6, column=3, value="Wohnort")
    sheet.cell(row=6, column=11, value="Arbeitsort")
    for number in range(1, 14):
        sheet.cell(row=8, column=2 + number, value=number)


def _rows(sheet, rows) -> None:
    for offset, (key, name, values) in enumerate(rows):
        row = 10 + 2 * offset  # the BA file has an empty row after each region
        sheet.cell(row=row, column=1, value=key)
        sheet.cell(row=row, column=2, value=f" {name}")
        for column, value in enumerate(values, start=3):
            sheet.cell(row=row, column=column, value=value)
    sheet.cell(row=10 + 2 * len(rows), column=1, value="© Statistik der Bundesagentur für Arbeit")


def build_workbook(
    path: Path,
    *,
    imprint_date: date = REFERENCE_DATE,
    kreis: tuple = KREIS,
    laender_land: tuple = LAND,
    column_numbers: bool = True,
) -> Path:
    workbook = openpyxl.Workbook()
    imprint = workbook.active
    imprint.title = "Impressum"
    imprint.cell(row=4, column=1, value="Impressum")
    imprint.cell(row=14, column=1, value="Stichtag:")
    imprint.cell(row=14, column=2, value=datetime.combine(imprint_date, datetime.min.time()))

    data = workbook.create_sheet("Gemeindedaten")
    _header(data)
    if not column_numbers:
        data.cell(row=8, column=3, value="Spalte 1")
    _rows(
        data,
        [
            ("99001001", "Fixdorf A", GEM_A),
            ("99001002", "Fixdorf B", GEM_B),
            ("99001005", "Neudorf", GEM_C),
            ("99001", "Fixkreis", kreis),
            ("99002000", "Fixstadt", STADT),
            ("991", "Reg.-Bez. Fix", LAND),
            ("99", "Fixland", LAND),
        ],
    )
    laender = workbook.create_sheet("Länder")
    _header(laender)
    _rows(
        laender,
        [
            ("99", "Fixland", laender_land),
            ("keine Angabe/keine Zuordnung möglich", " ", UNASSIGNED),
            (" ", "Deutschland", GERMANY),
        ],
    )
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# Parser and structural contract
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_read_workbook_classifies_levels_and_keeps_suppression_signs(tmp_path) -> None:
    content = read_workbook(build_workbook(tmp_path / "ba.xlsx"), REFERENCE_DATE, CONTRACT)
    by_key = {(row.sheet_name, row.source_key): row for row in content.rows}

    assert by_key[("Gemeindedaten", "99001002")].source_level == "gem"
    assert by_key[("Gemeindedaten", "99001")].source_level == "krs"
    assert by_key[("Gemeindedaten", "991")].source_level == "rb"
    assert by_key[("Länder", "unassigned")].source_level == "unassigned"
    assert by_key[("Länder", "DG")].source_level == "sta"
    assert by_key[("Gemeindedaten", "99001002")].values[8] == "*"
    assert by_key[("Gemeindedaten", "99001001")].values[0] == "100"
    assert by_key[("Gemeindedaten", "99001001")].source_name == "Fixdorf A"


@pytest.mark.contract
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"imprint_date": date(1998, 6, 30)}, "reference date"),
        ({"column_numbers": False}, "column numbers"),
        ({"kreis": (150, "*") + KREIS[2:]}, "suppressed value above Gemeinde level"),
        ({"kreis": (150, "-") + KREIS[2:]}, "unexpected value"),
        ({"laender_land": (351,) + LAND[1:]}, "Land rows differ"),
    ],
)
def test_read_workbook_refuses_layout_changes(tmp_path, change, message) -> None:
    with pytest.raises(BaContractError, match=message):
        read_workbook(build_workbook(tmp_path / "ba.xlsx", **change), REFERENCE_DATE, CONTRACT)


@pytest.mark.contract
def test_indicator_ddl_is_additive_and_prefixed() -> None:
    code = "\n".join(
        line.split("--", 1)[0] for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines()
    ).upper()
    for forbidden in ("DROP ", "TRUNCATE", "ALTER ", "UPDATE ", "TEMPORARY"):
        assert forbidden not in code
    for target in re.findall(r"(?:DELETE FROM|INSERT INTO) (\S+)", code):
        assert target.split(".")[-1].startswith("DE_"), target
    for name in re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)", code):
        assert name.split(".")[-1].startswith("DE_"), name
    for name in re.findall(r"CREATE OR REPLACE (?:VIEW|FUNCTION) (\S+?)[\s(]", code):
        assert "DE_" in name, name


# ---------------------------------------------------------------------------
# Release sensor
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_release_sensor_requests_the_next_vintage_only_once_published(monkeypatch) -> None:
    import dagster as dg

    from src.dagster_pipeline.sensors import ba_gemeindedaten as sensor

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sensor, "database_connection", lambda **_: _Connection())
    monkeypatch.setattr(sensor, "latest_loaded_reference_date", lambda _: date(2025, 6, 30))
    asked: list[date] = []

    def published(answer):
        def check(session, reference_date):
            asked.append(reference_date)
            return answer
        return check

    monkeypatch.setattr(sensor, "vintage_is_published", published(False))
    skip = sensor.ba_gemeindedaten_release_sensor(dg.build_sensor_context())
    assert isinstance(skip, dg.SkipReason)

    monkeypatch.setattr(sensor, "vintage_is_published", published(True))
    request = sensor.ba_gemeindedaten_release_sensor(dg.build_sensor_context())
    assert asked == [date(2026, 6, 30), date(2026, 6, 30)]
    assert request.run_key == "ba_gemeindedaten:2026-06-30"
    configs = [op["config"]["reference_date"] for op in request.run_config["ops"].values()]
    assert configs == ["2026-06-30", "2026-06-30"]


# ---------------------------------------------------------------------------
# Export and page
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_employment_record_keeps_missing_values_explicit() -> None:
    from src.export_germany_dashboard import EMPLOYMENT_MEASURES, employment_record

    suppressed = ("gem", "990010000002", REFERENCE_DATE, FIXTURE,
                  None, "suppressed", 50.0, "value", None, "suppressed",
                  40.0, "value", None, "suppressed",
                  None, None, "suppressed")
    record = employment_record(suppressed)
    assert record["jobs_ratio"] is None and record["jobs_ratio_status"] == "suppressed"
    assert record["employees_residence"] == 50 and record["employees_workplace"] is None
    assert record["status"] == {
        "employees_workplace": "suppressed",
        "commuters_in": "suppressed",
        "establishments": "suppressed",
    }
    assert len(EMPLOYMENT_MEASURES) == 5
    with pytest.raises(ValueError):
        employment_record(suppressed[:-2] + (1.0, "suppressed"))  # ratio with a suppressed status


@pytest.mark.contract
def test_page_offers_the_employment_theme_with_ba_attribution() -> None:
    from src.export_germany_dashboard import DASHBOARD_SOURCE

    html = (DASHBOARD_SOURCE / "index.html").read_text(encoding="utf-8")
    script = (DASHBOARD_SOURCE / "app.js").read_text(encoding="utf-8")
    for theme in ("temperature", "employment"):
        assert f'type="radio" name="theme" value="{theme}"' in html
    # BA terms of use: name the source, link the statistics homepage, mark
    # own calculations.
    assert "Statistik der Bundesagentur für Arbeit" in html
    assert 'href="https://statistik.arbeitsagentur.de/"' in html
    assert "eigene Berechnung" in html and "eigene Berechnung" in script
    assert "data/employment.json" in script


# ---------------------------------------------------------------------------
# Integration: synthetic workbook through raw, refresh and checks
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_refresh_serves_published_values_with_explicit_status(tmp_path) -> None:
    from src.database.connection import database_connection
    from src.database.load import copy_rows
    from src.germany.ba_gemeindedaten import apply_indicator_schema
    from src.germany.indicators import upsert_ba_catalogue

    path = build_workbook(tmp_path / "ba.xlsx")
    content = read_workbook(path, REFERENCE_DATE, CONTRACT)
    x, y = 4_500_000.0, 3_200_000.0
    square = f"SRID=3035;MULTIPOLYGON((({x} {y},{x + 1000} {y},{x + 1000} {y + 1000},{x} {y + 1000},{x} {y})))"

    with database_connection(application_name="lwr_germany_ba_test") as connection:
        with connection.transaction(force_rollback=True):
            apply_indicator_schema(connection)
            upsert_ba_catalogue(connection)
            connection.execute(
                "INSERT INTO normalized.de_land VALUES (%s,'99','Fixland',%s,4e6,'f')",
                (FIXTURE, square),
            )
            for kreis in ("99001", "99002"):
                connection.execute(
                    "INSERT INTO normalized.de_kreis VALUES (%s,%s,'99',%s,'Kreis',NULL,%s,2e6,'f')",
                    (FIXTURE, kreis, f"Kreis {kreis}", square),
                )
            for ars, ags, unincorporated in (
                ("990010000001", "99001001", False),
                ("990010000002", "99001002", False),
                ("990010000003", "99001003", True),   # unincorporated, not in BA
                ("990010000004", "99001004", False),  # merged after the geography version
                ("990020000000", "99002000", False),  # kreisfreie Stadt
            ):
                connection.execute(
                    "INSERT INTO normalized.de_gemeinde VALUES "
                    "(%s,%s,%s,left(%s,5),'99','G','Gemeinde',%s,%s,1e6,'f')",
                    (FIXTURE, ars, ags, ars, unincorporated, square),
                )
            copy_rows(
                connection,
                schema="raw",
                table="de_ba_gemeindedaten",
                columns=RAW_COLUMNS,
                rows=raw_rows(content, path=path, sha256="fixture", contract=CONTRACT),
            )
            summary = dict(
                ((level, status), count)
                for level, status, count in connection.execute(
                    "SELECT * FROM normalized.refresh_de_ba_indicators(%s, %s)",
                    (REFERENCE_DATE, FIXTURE),
                )
            )
            values = {
                (level, key, code): (value, status, reason, source_key)
                for level, key, code, value, status, reason, source_key in connection.execute(
                    """
                    SELECT level, region_key, indicator_code, value, value_status,
                           status_reason, source_key
                    FROM normalized.de_indicator_value
                    WHERE geography_version = %s AND reference_date = %s
                    """,
                    (FIXTURE, REFERENCE_DATE),
                )
            }
            coverage = connection.execute(
                "SELECT issue, level, region_key FROM normalized.check_de_ba_key_coverage(%s, %s)",
                (REFERENCE_DATE, FIXTURE),
            ).fetchall()
            totals = connection.execute(
                "SELECT * FROM normalized.check_de_ba_source_totals(%s)", (REFERENCE_DATE,)
            ).fetchall()
            identities_before = connection.execute(
                "SELECT * FROM normalized.check_de_ba_identities(%s)", (REFERENCE_DATE,)
            ).fetchall()
            connection.execute(
                "UPDATE raw.de_ba_gemeindedaten SET column_02 = '61' "
                "WHERE reference_date = %s AND source_key = '99001001'",
                (REFERENCE_DATE,),
            )
            identities_after = connection.execute(
                "SELECT rule, source_key FROM normalized.check_de_ba_identities(%s)",
                (REFERENCE_DATE,),
            ).fetchall()

    # Every region of the hierarchy has a row per indicator: 5 Gemeinden,
    # 2 Kreise, 1 Land and Germany, 13 indicators each.
    assert sum(summary.values()) == 9 * 13
    assert summary[("gem", "not_reported")] == 2 * 13
    workplace = {key: values[(level, key, "employees_workplace")]
                 for level, key, _ in values if _ == "employees_workplace"}
    assert workplace["990010000001"][:2] == (150.0, "value")
    assert workplace["990010000002"][:2] == (None, "suppressed")
    assert workplace["990010000003"][1:3] == ("not_reported", "no_source_record_unincorporated")
    assert workplace["990010000004"][1:3] == ("not_reported", "no_source_record")
    # Kreis from its own row; the kreisfreie Stadt from its Gemeinde row; the
    # published Kreis value includes the suppressed Gemeinde value.
    assert workplace["99001"] == (170.0, "value", None, "99001")
    assert workplace["99002"] == (300.0, "value", None, "99002000")
    assert workplace["99"][:2] == (470.0, "value")
    assert workplace["DG"][:2] == (475.0, "value")

    assert sorted(coverage) == [
        ("no_source_record", "gem", "99001004"),
        ("no_source_record_unincorporated", "gem", "99001003"),
        ("source_without_geography", "gem", "99001005"),
    ]
    # Exact where nothing is suppressed; never below the visible sum.
    for comparison, key, code, published, summed, suppressed in totals:
        if suppressed:
            assert published >= summed, (comparison, key, code)
        else:
            assert published == summed, (comparison, key, code)
    assert {row[0] for row in totals} == {"gem_to_krs", "krs_to_lan", "lan_to_sta"}
    assert identities_before == []
    assert identities_after == [("residence = men + women", "99001001")]
