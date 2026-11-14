"""Germany layer, third theme: employees by sector (BA Regionalreport per Kreis,
BA WZ booklets per Land).

Synthetic workbooks with the delivered layouts drive the parser tests and,
inside a rolled-back transaction, the raw load, the refresh and the check
functions. Their numbers are consistent: sections add up to the Kreis
total, the Kreise to the Land, and the Land's WZ divisions to the same
groups as its Kreise. Kreis 99002 has suppressed cells.
"""

import re
from datetime import date

import openpyxl
import pytest

from src.germany.ba_sectors import (
    BaSectorContractError,
    PoliteClient,
    REGIONALREPORT_COLUMNS,
    SCHEMA_PATH,
    WZ_COLUMNS,
    next_quarter,
    previous_quarter,
    read_regionalreport,
    read_wz_heft,
    regionalreport_rows,
    wz_rows,
)
from src.germany.contract import BA_REGIONALREPORT_CONTRACT


FILE_DATE = date(1999, 3, 31)  # no real quarter uses it
QUARTER_LABELS = ["Mrz. 99", "Dez. 98", "Sep. 98", "Jun. 98", "Mrz. 98"]
FIXTURE = "fixture-sec"
# Table 2.3 values per position (see the contract's sector_rows).
KREIS_A = [100, 1, 2, 30, 5, 20, 5, 3, 10, 4, 2, 3, 1, 5, 8, 6, 2, 6, 5, 10, 7, 3, 0, 1, 35, 64]
KREIS_B = [50, "*", 1, 20, 2, 15, 3, 2, 6, 2, 1, 1, 1, 3, 3, "*", "*", 2, 2, 3, 2, 1, 0, "*", 23, 27]
LAND_TOTAL = 150
# WZ booklet of the Land (and of Germany, which here has only this Land):
# groups as in the Kreise: consumer 7, metal/electrical/steel 35,
# intermediate 8; other divisions 100.
WZ_DIVISIONS = {"01": 100, "10": 7, "20": 8, "24": 5, "28": 10, "29": 20}
WZ_GROUPS = {"241": 3}


def _imprint(workbook, fields: dict[str, str]) -> None:
    sheet = workbook.active
    sheet.title = "Impressum"
    sheet.cell(row=4, column=1, value="Impressum")
    for offset, (name, value) in enumerate(fields.items()):
        sheet.cell(row=6 + offset, column=1, value=f"{name}:")
        sheet.cell(row=6 + offset, column=2, value=value)


def _quarter_header(sheet, labels=QUARTER_LABELS) -> None:
    sheet.cell(row=7, column=1, value="Merkmale")
    for column, label in enumerate(labels, start=4):
        sheet.cell(row=8, column=column, value=label)
        sheet.cell(row=10, column=column, value=column - 3)


def build_regionalreport(path, kreis_key, values, *, berichtsmonat="31. März 1999",
                         region=None, labels=QUARTER_LABELS, swap_rows=False):
    workbook = openpyxl.Workbook()
    _imprint(workbook, {
        "Produkt-ID": "709",
        "Region": region or f"Fixkreis ({kreis_key})",
        "Berichtsmonat": berichtsmonat,
    })
    table = workbook.create_sheet("Tabelle 2.3")
    _quarter_header(table, labels)
    rows = list(BA_REGIONALREPORT_CONTRACT.sector_rows)
    if swap_rows:
        rows[1], rows[2] = rows[2], rows[1]
    row_number = 11
    for position, (code, _, label) in enumerate(rows):
        if position == len(rows) - 3:
            table.cell(row=row_number, column=1, value="davon nach Sektoren:")
            row_number += 1
        table.cell(row=row_number, column=1, value=code or None)
        table.cell(row=row_number, column=2, value=label)
        for column in range(4, 9):
            table.cell(row=row_number, column=column, value=values[position])
        row_number += 1
    table.cell(row=row_number + 1, column=1, value="1) Anteil an der jeweiligen Gesamtsumme")
    totals = workbook.create_sheet("Tabelle 2.1")
    _quarter_header(totals, labels)
    row_number = 11
    for region_label, total in (
        (region or f"Fixkreis ({kreis_key})", values[0]),
        ("Bundesland Fixland", LAND_TOTAL),
        ("Deutschland", LAND_TOTAL),
    ):
        totals.cell(row=row_number, column=1, value=region_label)
        totals.cell(row=row_number + 1, column=1, value="Insgesamt")
        for column in range(4, 9):
            totals.cell(row=row_number + 1, column=column, value=total)
        row_number += 2
    workbook.save(path)
    return path


def build_wz_heft(path, region_name, *, stichtag="31. März 1999", duplicate=False):
    workbook = openpyxl.Workbook()
    _imprint(workbook, {"Region": region_name, "Stichtag": stichtag})
    sheet = workbook.create_sheet("SVB - Tabelle I")
    for column in range(2, 13):
        sheet.cell(row=11, column=column, value=column - 1)
    rows = [("Insgesamt", LAND_TOTAL), ("davon:", None)]
    # Every manufacturing division (10-33), as in a real booklet; 0 where unused.
    for code in sorted({*WZ_DIVISIONS, *WZ_GROUPS, *(f"{n}" for n in range(10, 34))}):
        rows.append((f"{code} Wirtschaftszweig {code}", WZ_DIVISIONS.get(code, WZ_GROUPS.get(code, 0))))
    if duplicate:
        rows.append(("10 Wirtschaftszweig 10", 1))
    rows.append(("XX Keine Angabe", 0))
    for offset, (label, value) in enumerate(rows):
        sheet.cell(row=12 + offset, column=1, value=label)
        if value is not None:
            for column in range(2, 13):
                sheet.cell(row=12 + offset, column=column, value=value)
    sheet.cell(row=14 + len(rows), column=1, value="*) Aus Datenschutzgründen")
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# Parsers and structural contracts
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_regionalreport_parser_reads_five_quarters_and_keeps_suppression(tmp_path) -> None:
    rows = read_regionalreport(
        build_regionalreport(tmp_path / "r.xlsx", "99002", KREIS_B), "99002", FILE_DATE
    )
    table = [row for row in rows if row.table_name == "2.3"]
    assert len(table) == len(BA_REGIONALREPORT_CONTRACT.sector_rows) * 5
    assert {row.quarter_date for row in table} == {
        date(1999, 3, 31), date(1998, 12, 31), date(1998, 9, 30), date(1998, 6, 30), date(1998, 3, 31)
    }
    assert [row.value for row in table if row.row_code == "782.783"] == ["*"] * 5
    assert {row.region_label for row in rows if row.table_name == "2.1"} == {
        "Fixkreis (99002)", "Bundesland Fixland", "Deutschland"
    }


@pytest.mark.contract
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"berichtsmonat": "31. Dezember 1998"}, "Berichtsmonat"),
        ({"region": "Anderer Kreis (99009)"}, "expected Kreis 99001"),
        ({"swap_rows": True}, "Table 2.3 rows"),
        ({"labels": ["Mrz. 99", "Sep. 98", "Jun. 98", "Mrz. 98", "Dez. 97"]}, "quarters"),
    ],
)
def test_regionalreport_parser_refuses_layout_changes(tmp_path, change, message) -> None:
    path = build_regionalreport(tmp_path / "r.xlsx", "99001", KREIS_A, **change)
    with pytest.raises(BaSectorContractError, match=message):
        read_regionalreport(path, "99001", FILE_DATE)


@pytest.mark.contract
def test_wz_parser_reads_codes_and_refuses_changes(tmp_path) -> None:
    booklet = read_wz_heft(build_wz_heft(tmp_path / "w.xlsx", "Fixland"), FILE_DATE)
    codes = {row.wz_code: row.values[0] for row in booklet.rows}
    assert codes["total"] == "150" and codes["241"] == "3" and codes["XX"] == "0"
    assert booklet.region_name == "Fixland"
    with pytest.raises(BaSectorContractError, match="Stichtag"):
        read_wz_heft(build_wz_heft(tmp_path / "a.xlsx", "Fixland", stichtag="30. Juni 1999"), FILE_DATE)
    with pytest.raises(BaSectorContractError, match="duplicate"):
        read_wz_heft(build_wz_heft(tmp_path / "b.xlsx", "Fixland", duplicate=True), FILE_DATE)


@pytest.mark.contract
def test_quarter_arithmetic_crosses_the_year() -> None:
    assert previous_quarter(date(2026, 3, 31)) == date(2025, 12, 31)
    assert next_quarter(date(2025, 12, 31)) == date(2026, 3, 31)
    assert next_quarter(date(2026, 3, 31)) == date(2026, 6, 30)


@pytest.mark.contract
def test_sector_ddl_is_additive_and_prefixed() -> None:
    code = "\n".join(
        line.split("--", 1)[0] for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines()
    ).upper()
    for forbidden in ("DROP ", "TRUNCATE", "ALTER ", "UPDATE ", "TEMPORARY TABLE"):
        assert forbidden not in code
    for target in re.findall(r"(?:DELETE FROM|INSERT INTO) (\S+)", code):
        assert target.split(".")[-1].startswith("DE_"), target
    for name in re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)", code):
        assert name.split(".")[-1].startswith("DE_"), name
    for name in re.findall(r"CREATE OR REPLACE (?:VIEW|FUNCTION) (\S+?)[\s(]", code):
        assert "DE_" in name, name


# ---------------------------------------------------------------------------
# Considerate HTTP client
# ---------------------------------------------------------------------------
class _Response:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}


class _Session:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def request(self, method, url, timeout, allow_redirects):
        self.calls += 1
        status = self.statuses.pop(0)
        return status if isinstance(status, _Response) else _Response(status)


@pytest.mark.contract
def test_polite_client_pauses_retries_and_gives_up(monkeypatch, tmp_path) -> None:
    import src.germany.ba_sectors as module

    clock = {"now": 0.0}
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

    def sleep(seconds):
        sleeps.append(round(seconds, 2))
        clock["now"] += seconds

    monkeypatch.setattr(module.time, "sleep", sleep)

    cooldown = tmp_path / "ba_rate_limit.json"
    session = _Session([200, 200])
    client = PoliteClient(session, pause_seconds=1.5, cooldown_path=cooldown)
    client.request("GET", "u1")
    client.request("GET", "u2")
    assert sleeps == [1.5]  # pause between two requests, none before the first

    sleeps.clear()
    session = _Session([503, _Response(429, {"Retry-After": "7"}), 200])
    client = PoliteClient(session, pause_seconds=0, backoff_seconds=(10.0, 30.0), cooldown_path=cooldown)
    assert client.request("GET", "u").status_code == 200
    assert session.calls == 3 and sleeps == [10.0, 7.0]

    session = _Session([503, 503])
    client = PoliteClient(session, pause_seconds=0, max_attempts=2, backoff_seconds=(1.0,),
                          cooldown_path=cooldown)
    assert client.request("GET", "u").status_code == 503  # caller sees the failure
    assert session.calls == 2


@pytest.mark.contract
def test_rate_limit_waits_minutes_then_stops_everyone_for_the_cooldown(monkeypatch, tmp_path) -> None:
    import src.germany.ba_sectors as module
    from src.germany.ba_sectors import BaRateLimited, cooldown_until

    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    cooldown = tmp_path / "ba_rate_limit.json"

    # 429 without Retry-After: minutes between attempts, then a recorded
    # cool-down and a clear stop instead of a returned error page.
    session = _Session([429, 429, 429, 429])
    client = PoliteClient(session, pause_seconds=0, rate_limit_waits=(60.0, 300.0, 900.0),
                          cooldown_seconds=3600.0, cooldown_path=cooldown)
    with pytest.raises(BaRateLimited, match="run again after"):
        client.request("GET", "u")
    assert session.calls == 4 and sleeps == [60.0, 300.0, 900.0]
    assert cooldown_until(cooldown) is not None

    # Any later client (the other raw asset, the sensor, the next run) sends
    # nothing while the cool-down lasts.
    later = _Session([200])
    with pytest.raises(BaRateLimited, match="no requests before"):
        PoliteClient(later, pause_seconds=0, cooldown_path=cooldown).request("GET", "u")
    assert later.calls == 0

    # A run never exceeds its request budget.
    budget = _Session([200, 200, 200])
    client = PoliteClient(budget, pause_seconds=0, max_requests=2, cooldown_path=tmp_path / "other.json")
    client.request("GET", "a")
    client.request("GET", "b")
    with pytest.raises(BaRateLimited, match="budget"):
        client.request("GET", "c")
    assert budget.calls == 2


@pytest.mark.contract
def test_run_sectors_and_sensor_respect_an_active_cooldown(monkeypatch, tmp_path) -> None:
    import dagster as dg

    import src.germany.run_sectors as run
    from src.dagster_pipeline.sensors import ba_sectors as sensor
    from src.germany.ba_sectors import _record_cooldown

    _record_cooldown(tmp_path / "data/raw/germany/ba_rate_limit.json", 600, "u")
    monkeypatch.setattr(run, "ensure_dagster_home", lambda root: None)
    monkeypatch.setattr(run, "execute_job", lambda *a, **k: pytest.fail("job must not start"))
    with pytest.raises(run.SectorRunRateLimited, match="Run the same command again after"):
        run.run_germany_sectors(date(2026, 3, 31), project_root=tmp_path)

    monkeypatch.setattr(sensor, "cooldown_until", lambda: run.cooldown_until(tmp_path / "data/raw/germany/ba_rate_limit.json"))
    monkeypatch.setattr(sensor, "latest_loaded_file_date", lambda: pytest.fail("no lookup during a cool-down"))
    assert isinstance(sensor.ba_sectors_release_sensor(dg.build_sensor_context()), dg.SkipReason)


@pytest.mark.contract
def test_sector_sensor_requests_the_next_quarter_once_both_products_exist(monkeypatch) -> None:
    import dagster as dg

    from src.dagster_pipeline.sensors import ba_sectors as sensor

    monkeypatch.setattr(sensor, "latest_loaded_file_date", lambda: date(2026, 3, 31))
    monkeypatch.setattr(sensor, "quarter_is_published", lambda client, day: False)
    assert isinstance(sensor.ba_sectors_release_sensor(dg.build_sensor_context()), dg.SkipReason)
    asked = []
    monkeypatch.setattr(sensor, "quarter_is_published", lambda client, day: asked.append(day) or True)
    request = sensor.ba_sectors_release_sensor(dg.build_sensor_context())
    assert asked == [date(2026, 6, 30)]
    assert request.run_key == "ba_sectors:2026-06-30"
    assert {op["config"]["file_date"] for op in request.run_config["ops"].values()} == {"2026-06-30"}


# ---------------------------------------------------------------------------
# Export and page
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_sector_record_leaves_out_what_the_level_does_not_publish() -> None:
    from src.export_germany_dashboard import SECTOR_GROUPS, SECTOR_MEASURES, sector_record

    def row(level, statuses, share, share_status, bases=(None,) * 4, note=None):
        values = []
        for name in SECTOR_MEASURES:
            status = statuses.get(name, "value")
            values += [10.0 if status == "value" else None, status]
        return (level, "99001", FILE_DATE, *values, share, share_status, *bases, note)

    kreis = sector_record(row("krs", {m: "not_published_at_level" for m in
                                      ("motor_vehicles", "machinery", "chemicals", "steel")},
                              0.2, "value", note="Hinweis"))
    assert "motor_vehicles" not in kreis and kreis["metal_electrical_steel"] == 10
    assert kreis["quality_note"] == "Hinweis" and "basis" not in kreis
    land = sector_record(row("lan", {"metal_electrical_steel": "suppressed"}, None, "suppressed",
                             bases=("sum_of_divisions", "sum_of_kreise", None, None)))
    assert land["metal_electrical_steel"] is None
    assert land["status"] == {"metal_electrical_steel": "suppressed"}
    assert land["basis"] == {"manufacturing": "sum_of_divisions", "metal_electrical_steel": "sum_of_kreise"}
    assert len(SECTOR_GROUPS) == 4
    with pytest.raises(ValueError):
        sector_record(row("krs", {}, 0.2, "suppressed"))


@pytest.mark.contract
def test_page_offers_the_sector_theme_and_states_its_limits() -> None:
    from src.export_germany_dashboard import DASHBOARD_SOURCE

    html = (DASHBOARD_SOURCE / "index.html").read_text(encoding="utf-8")
    script = (DASHBOARD_SOURCE / "app.js").read_text(encoding="utf-8")
    assert 'type="radio" name="theme" value="sectors"' in html
    assert "keine Branchenzahlen" in html
    assert "Regionalreport über Beschäftigte" in html
    assert "Beschäftigte nach Wirtschaftszweigen" in html
    assert "nur für Länder veröffentlicht" in script
    assert "data/sectors.json" in script


# ---------------------------------------------------------------------------
# Integration: synthetic workbooks through raw, refresh and checks
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sector_refresh_and_checks_on_a_fixture_land(tmp_path) -> None:
    from src.database.connection import database_connection
    from src.database.load import copy_rows
    from src.germany.ba_sectors import apply_sector_schema
    from src.germany.indicators import sector_row_indicators, upsert_sector_catalogue

    reports = {
        "99001": build_regionalreport(tmp_path / "a.xlsx", "99001", KREIS_A),
        "99002": build_regionalreport(tmp_path / "b.xlsx", "99002", KREIS_B),
    }
    booklets = {"99": build_wz_heft(tmp_path / "l.xlsx", "Fixland"),
                "d": build_wz_heft(tmp_path / "d.xlsx", "Deutschland")}
    square = "SRID=3035;MULTIPOLYGON(((4500000 3200000,4501000 3200000,4501000 3201000,4500000 3201000,4500000 3200000)))"

    with database_connection(application_name="lwr_germany_sector_test") as connection:
        with connection.transaction(force_rollback=True):
            apply_sector_schema(connection)
            connection.execute(
                "INSERT INTO normalized.de_land VALUES (%s,'99','Fixland',%s,2e6,'f')", (FIXTURE, square)
            )
            for kreis in reports:
                connection.execute(
                    "INSERT INTO normalized.de_kreis VALUES (%s,%s,'99',%s,'Kreis',NULL,%s,1e6,'f')",
                    (FIXTURE, kreis, f"Kreis {kreis}", square),
                )
            copy_rows(connection, schema="raw", table="de_ba_regionalreport",
                      columns=REGIONALREPORT_COLUMNS,
                      rows=(row for key, path in reports.items()
                            for row in regionalreport_rows(path, key, FILE_DATE)))
            from src.germany.contract import BA_WZ_HEFT_CONTRACT
            from dataclasses import replace
            fixture_contract = replace(BA_WZ_HEFT_CONTRACT, regions={"99": "99", "d": "DG"})
            copy_rows(connection, schema="raw", table="de_ba_wz_heft", columns=WZ_COLUMNS,
                      rows=(row for code, path in booklets.items()
                            for row in wz_rows(path, code, FILE_DATE, fixture_contract)))
            upsert_sector_catalogue(connection)
            connection.execute("ANALYZE raw.de_ba_regionalreport")
            connection.execute(
                "SELECT * FROM normalized.refresh_de_ba_sectors(%s, %s)",
                (FIXTURE, sector_row_indicators()),
            ).fetchall()
            values = {
                (level, key, code, quarter): (value, status)
                for level, key, code, quarter, value, status in connection.execute(
                    """
                    SELECT level, region_key, indicator_code, reference_date, value, value_status
                    FROM normalized.de_indicator_value
                    WHERE geography_version = %s AND reference_date BETWEEN '1998-01-01' AND '1999-12-31'
                    """,
                    (FIXTURE,),
                )
            }
            identities = connection.execute(
                "SELECT * FROM normalized.check_de_ba_sector_identities(%s)", (FILE_DATE,)
            ).fetchall()
            totals = connection.execute(
                "SELECT * FROM normalized.check_de_ba_sector_totals(%s, %s)", (FILE_DATE, FIXTURE)
            ).fetchall()

    quarter = date(1999, 3, 31)
    assert values[("krs", "99001", "sector_c_metal_electrical_steel", quarter)] == (20.0, "value")
    assert values[("krs", "99002", "sector_temporary_agency", quarter)] == (None, "suppressed")
    assert values[("krs", "99002", "sector_total", date(1998, 3, 31))] == (50.0, "value")
    assert values[("lan", "99", "wz_29", quarter)] == (20.0, "value")
    assert values[("sta", "DG", "wz_241", quarter)] == (3.0, "value")
    assert ("krs", "99001", "wz_29", quarter) not in values  # Land-only indicator
    assert identities == []
    by_comparison: dict[str, list] = {}
    for comparison, key, item, day, published, summed, suppressed in totals:
        if day.year > 1999:
            continue  # real quarters loaded in the database; their rules are in the Dagster check
        by_comparison.setdefault(comparison, []).append((key, item, published, summed, suppressed))
        if suppressed:
            assert published >= summed, (comparison, key, item)
        else:
            assert published == summed, (comparison, key, item)
    assert {"kreise_to_land", "laender_to_germany", "land_report_vs_wz", "kreise_group_vs_wz",
            "wz_divisions_to_total", "wz_laender_to_germany"} <= set(by_comparison)
    groups = {item: (published, summed) for key, item, published, summed, _ in by_comparison["kreise_group_vs_wz"]}
    assert groups["wz_sum_metal_electrical_steel"] == (35, 35)
    assert groups["wz_sum_c"] == (50, 50)
