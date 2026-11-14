"""Sector data for the Germany layer (ADR 020): BA Regionalreport per Kreis
(sections and three manufacturing groups) and BA Beschäftigte nach
Wirtschaftszweigen per Land and Germany (WZ divisions and groups).

Usage:
    uv run --env-file .env python -m src.germany.ba_sectors --file-date 2026-03-31

Both products are quarterly xlsx downloads without login. The Regionalreport
is one file per Kreis, so a quarter means 400 requests: they are sent one
after another with a pause, retried with backoff on 429/5xx, and never
repeated for a file already on disk. Every file is checked against the
structural contract before it is kept; one bad file stops the run.
"""

import argparse
import calendar
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import openpyxl
import requests
from psycopg import Connection

from src.database.connection import database_connection
from src.database.load import copy_rows
from src.germany.acquire import AcquiredFile, sha256_file
from src.germany.contract import (
    BA_REGIONALREPORT_CONTRACT,
    BA_WZ_HEFT_CONTRACT,
    BaRegionalreportContract,
    BaWzHeftContract,
)
from src.germany.raw_load import RawLoadResult


LOGGER = logging.getLogger("germany_ba_sectors")
TIMEOUT_SECONDS = 120
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VALUE_PATTERN = re.compile(r"^[0-9]+$")
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "germany" / "sector_schema.sql"
)
GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "dezember": 12,
}
QUARTER_LABEL_MONTHS = {"mrz": 3, "jun": 6, "sep": 9, "dez": 12}


class BaSectorContractError(ValueError):
    """A sector workbook does not have the layout the pipeline was built for."""


# ---------------------------------------------------------------------------
# Considerate HTTP: one request at a time, a pause between requests, backoff
# ---------------------------------------------------------------------------
@dataclass
class PoliteClient:
    session: requests.Session
    pause_seconds: float = 1.5
    max_attempts: int = 4
    backoff_seconds: tuple[float, ...] = (10.0, 30.0, 90.0)
    _last_request: float | None = None
    requests_sent: int = 0

    def _wait(self) -> None:
        if self._last_request is None:
            return
        delay = self._last_request + self.pause_seconds - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def request(self, method: str, url: str) -> requests.Response:
        for attempt in range(1, self.max_attempts + 1):
            self._wait()
            self._last_request = time.monotonic()
            self.requests_sent += 1
            try:
                response = self.session.request(
                    method, url, timeout=TIMEOUT_SECONDS, allow_redirects=True
                )
            except requests.RequestException as error:
                if attempt == self.max_attempts:
                    raise
                LOGGER.warning("%s %s failed (%s); retrying", method, url, error)
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                if attempt == self.max_attempts:
                    return response
                LOGGER.warning("%s %s: HTTP %s; retrying", method, url, response.status_code)
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(min(float(retry_after), 600.0))
                    continue
            time.sleep(self.backoff_seconds[min(attempt, len(self.backoff_seconds)) - 1])
        raise AssertionError("unreachable")

    def is_published(self, url: str) -> bool:
        response = self.request("HEAD", url)
        return (
            response.status_code == 200
            and response.headers.get("Content-Type", "").startswith(XLSX_CONTENT_TYPE)
        )


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def quarter_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def previous_quarter(day: date) -> date:
    month = day.month - 3
    year = day.year
    if month <= 0:
        month += 12
        year -= 1
    return quarter_end(year, month)


def next_quarter(day: date) -> date:
    month = day.month + 3
    year = day.year
    if month > 12:
        month -= 12
        year += 1
    return quarter_end(year, month)


def parse_german_date(text: str) -> date:
    """'31. März 2026' -> date(2026, 3, 31)."""
    match = re.fullmatch(r"(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(\d{4})", " ".join(str(text).split()))
    if not match or match.group(2).lower() not in GERMAN_MONTHS:
        raise BaSectorContractError(f"Unexpected date {text!r}")
    return date(int(match.group(3)), GERMAN_MONTHS[match.group(2).lower()], int(match.group(1)))


def parse_quarter_label(text: str, not_after: date) -> date:
    """'Mrz. 26' -> date(2026, 3, 31). The century is the one that puts the
    year closest to, but not after, the file's reference date."""
    match = re.fullmatch(r"([A-Za-z]+)\.?\s*(\d{2})", " ".join(str(text).split()))
    if not match or match.group(1).lower() not in QUARTER_LABEL_MONTHS:
        raise BaSectorContractError(f"Unexpected quarter label {text!r}")
    year = not_after.year // 100 * 100 + int(match.group(2))
    if year > not_after.year:
        year -= 100
    return quarter_end(year, QUARTER_LABEL_MONTHS[match.group(1).lower()])


# ---------------------------------------------------------------------------
# Workbook helpers
# ---------------------------------------------------------------------------
def _collapse(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _value(cell: object, where: str) -> str:
    if isinstance(cell, bool):
        raise BaSectorContractError(f"{where}: unexpected {cell!r}")
    if isinstance(cell, int) and cell >= 0:
        return str(cell)
    if isinstance(cell, float) and cell.is_integer() and cell >= 0:
        return str(int(cell))
    text = _collapse(cell)
    if text == "*" or VALUE_PATTERN.match(text):
        return text
    raise BaSectorContractError(f"{where}: unexpected value {cell!r}")


def _imprint(workbook) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in workbook["Impressum"].iter_rows(values_only=True):
        cells = [c for c in row if c is not None and _collapse(c)]
        if len(cells) >= 2 and _collapse(cells[0]).endswith(":"):
            value = cells[1]
            if isinstance(value, datetime):
                value = f"{value.day}. {list(GERMAN_MONTHS)[value.month - 1].capitalize()} {value.year}"
            fields.setdefault(_collapse(cells[0])[:-1], _collapse(value))
    return fields


def _open(path: Path):
    # A handle, not the path: openpyxl rejects names not ending in .xlsx
    # (downloads are checked as <name>.xlsx.part before they are renamed).
    handle = path.open("rb")
    return handle, openpyxl.load_workbook(handle, read_only=True, data_only=True)


# ---------------------------------------------------------------------------
# Regionalreport (one Kreis per file)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegionalreportRow:
    table_name: str
    sheet_row: int
    region_label: str
    row_code: str
    row_label: str
    quarter_date: date
    quarter_label: str
    value: str


def _quarter_columns(rows: list[tuple], table: str, file_date: date, quarters: int) -> tuple[int, list[tuple[date, str]]]:
    """Index of the column-number row and the quarter of each value column."""
    for index, row in enumerate(rows):
        numbers = [_collapse(c) for c in row[3:3 + quarters]]
        if numbers == [str(n) for n in range(1, quarters + 1)]:
            labels = [_collapse(c) for c in rows[index - 2][3:3 + quarters]]
            dates = [parse_quarter_label(label, file_date) for label in labels]
            expected = [file_date]
            while len(expected) < quarters:
                expected.append(previous_quarter(expected[-1]))
            if dates != expected:
                raise BaSectorContractError(f"Table {table}: quarters {labels}, expected {expected}")
            return index, list(zip(dates, labels))
    raise BaSectorContractError(f"Table {table}: no column-number row")


def read_regionalreport(
    path: Path,
    kreis_key: str,
    file_date: date,
    contract: BaRegionalreportContract = BA_REGIONALREPORT_CONTRACT,
) -> list[RegionalreportRow]:
    handle, workbook = _open(path)
    try:
        for sheet in ("Impressum", "Tabelle 2.1", "Tabelle 2.3"):
            if sheet not in workbook.sheetnames:
                raise BaSectorContractError(f"Sheet {sheet!r} missing")
        imprint = _imprint(workbook)
        if parse_german_date(imprint.get("Berichtsmonat", "")) != file_date:
            raise BaSectorContractError(f"Berichtsmonat {imprint.get('Berichtsmonat')!r}, expected {file_date}")
        if not imprint.get("Region", "").endswith(f"({kreis_key})"):
            raise BaSectorContractError(f"Region {imprint.get('Region')!r}, expected Kreis {kreis_key}")
        quarters = contract.quarters_per_file
        result: list[RegionalreportRow] = []

        # Table 2.3: sections and manufacturing groups of the Kreis.
        rows = list(workbook["Tabelle 2.3"].iter_rows(values_only=True))
        header, columns = _quarter_columns(rows, "2.3", file_date, quarters)
        delivered: list[tuple[int, str, str, tuple]] = []
        for index in range(header + 1, len(rows)):
            row = rows[index]
            code, label = _collapse(row[0]), _collapse(row[1])
            if code.startswith(("1)", "*)")) or code.startswith("Aus Datenschutz"):
                break
            if not code and not label:
                continue
            if code == "davon nach Sektoren:":
                continue
            delivered.append((index + 1, code, label, row[3:3 + quarters]))
        expected = [(code, label) for code, _, label in contract.sector_rows]
        got = [code for _, code, _, _ in delivered]
        if got != [code for code, _ in expected]:
            raise BaSectorContractError(f"Table 2.3 rows {got}")
        if delivered[[c for c, _ in expected].index("")][2] != "keine Zuordnung möglich":
            raise BaSectorContractError("Table 2.3: unassigned row moved")
        region = imprint["Region"]
        for (sheet_row, code, label, values), (_, _, contract_label) in zip(delivered, contract.sector_rows):
            for (quarter, quarter_label), cell in zip(columns, values):
                result.append(RegionalreportRow(
                    "2.3", sheet_row, region, code, label or contract_label, quarter, quarter_label,
                    _value(cell, f"2.3 row {sheet_row} {quarter_label}"),
                ))

        # Table 2.1: totals of the Kreis, its Land, West/East and Germany.
        rows = list(workbook["Tabelle 2.1"].iter_rows(values_only=True))
        header, columns = _quarter_columns(rows, "2.1", file_date, quarters)
        region_label = ""
        for index in range(header + 1, len(rows)):
            row = rows[index]
            code, label = _collapse(row[0]), _collapse(row[1])
            values = row[3:3 + quarters]
            if code.startswith(("1)", "*)")):
                break
            if not code and not label:
                continue
            if all(v is None or _collapse(v) == "" for v in values):
                region_label = code or label
                continue
            if not region_label:
                raise BaSectorContractError(f"Table 2.1 row {index + 1}: values before a region")
            for (quarter, quarter_label), cell in zip(columns, values):
                result.append(RegionalreportRow(
                    "2.1", index + 1, region_label, code, label or code, quarter, quarter_label,
                    _value(cell, f"2.1 row {index + 1} {quarter_label}"),
                ))
        regions = {row.region_label for row in result if row.table_name == "2.1"}
        if not any(r.endswith(f"({kreis_key})") for r in regions) or "Deutschland" not in regions:
            raise BaSectorContractError(f"Table 2.1 regions {sorted(regions)}")
        if not any(r.startswith("Bundesland ") for r in regions):
            raise BaSectorContractError(f"Table 2.1 has no Land: {sorted(regions)}")
        return result
    finally:
        workbook.close()
        handle.close()


# ---------------------------------------------------------------------------
# WZ booklets (one Land or Germany per file)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WzRow:
    sheet_row: int
    wz_code: str
    wz_label: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class WzBooklet:
    region_name: str
    rows: tuple[WzRow, ...]


def read_wz_heft(
    path: Path,
    file_date: date,
    contract: BaWzHeftContract = BA_WZ_HEFT_CONTRACT,
) -> WzBooklet:
    handle, workbook = _open(path)
    try:
        for sheet in ("Impressum", contract.sheet):
            if sheet not in workbook.sheetnames:
                raise BaSectorContractError(f"Sheet {sheet!r} missing")
        imprint = _imprint(workbook)
        if parse_german_date(imprint.get("Stichtag", "")) != file_date:
            raise BaSectorContractError(f"Stichtag {imprint.get('Stichtag')!r}, expected {file_date}")
        rows = list(workbook[contract.sheet].iter_rows(values_only=True))
        width = contract.value_columns
        header = next(
            (
                index for index, row in enumerate(rows)
                if [_collapse(c) for c in row[1:1 + width]] == [str(n) for n in range(1, width + 1)]
            ),
            None,
        )
        if header is None:
            raise BaSectorContractError(f"{contract.sheet}: no column-number row")
        result: list[WzRow] = []
        for index in range(header + 1, len(rows)):
            row = rows[index]
            first = _collapse(row[0])
            if first.startswith(("*)", "1)")):
                break
            if not first or first == "davon:":
                continue
            if first == "Insgesamt":
                code, label = "total", "Insgesamt"
            else:
                match = re.fullmatch(r"(\d{2,3}|XX)\s+(.+)", first)
                if not match:
                    raise BaSectorContractError(f"{contract.sheet} row {index + 1}: {first!r}")
                code, label = match.group(1), match.group(2)
            values = tuple(
                _value(cell, f"{contract.sheet} row {index + 1} column {number}")
                for number, cell in enumerate(row[1:1 + width], start=1)
            )
            result.append(WzRow(index + 1, code, label, values))
        codes = [row.wz_code for row in result]
        if codes.count("total") != 1 or len(codes) != len(set(codes)):
            raise BaSectorContractError("WZ table: duplicate or missing rows")
        return WzBooklet(imprint.get("Region", ""), tuple(result))
    finally:
        workbook.close()
        handle.close()


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------
def _download(client: PoliteClient, url: str, target: Path, check) -> AcquiredFile:
    if target.exists():
        return AcquiredFile(target, sha256_file(target), target.stat().st_size, False)
    response = client.request("GET", url)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not content_type.startswith(XLSX_CONTENT_TYPE):
        raise RuntimeError(f"{url}: HTTP {response.status_code}, Content-Type {content_type!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".xlsx.part")
    partial.write_bytes(response.content)
    try:
        check(partial)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    target.with_suffix(".xlsx.acquisition.json").write_text(
        json.dumps(
            {
                "source_url": url,
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return AcquiredFile(target, sha256_file(target), target.stat().st_size, True)


def acquire_regionalreports(
    client: PoliteClient,
    file_date: date,
    kreis_keys: list[str],
    project_root: Path = Path("."),
    contract: BaRegionalreportContract = BA_REGIONALREPORT_CONTRACT,
) -> list[AcquiredFile]:
    acquired = []
    for number, kreis_key in enumerate(kreis_keys, start=1):
        target = project_root / contract.path(kreis_key, file_date)
        item = _download(
            client,
            contract.url(kreis_key, file_date),
            target,
            lambda p, k=kreis_key: read_regionalreport(p, k, file_date, contract),
        )
        acquired.append(item)
        if item.downloaded and number % 50 == 0:
            LOGGER.info("Regionalreport %s: %d of %d files", file_date, number, len(kreis_keys))
    return acquired


def acquire_wz_hefte(
    client: PoliteClient,
    file_date: date,
    project_root: Path = Path("."),
    contract: BaWzHeftContract = BA_WZ_HEFT_CONTRACT,
) -> list[AcquiredFile]:
    return [
        _download(
            client,
            contract.url(code, file_date),
            project_root / contract.path(code, file_date),
            lambda p: read_wz_heft(p, file_date, contract),
        )
        for code in contract.regions
    ]


# ---------------------------------------------------------------------------
# Raw load
# ---------------------------------------------------------------------------
REGIONALREPORT_COLUMNS = (
    "file_date", "kreis_key", "table_name", "sheet_row", "region_label",
    "row_code", "row_label", "quarter_date", "quarter_label", "value",
    "source_path", "source_sha256", "source_url",
)
WZ_COLUMNS = (
    "file_date", "region_key", "region_name", "sheet_row", "wz_code", "wz_label",
    "column_values", "source_path", "source_sha256", "source_url",
)


def apply_sector_schema(connection: Connection) -> None:
    from src.germany.ba_gemeindedaten import apply_indicator_schema

    apply_indicator_schema(connection)
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def kreis_keys(connection: Connection, geography_version: str) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT kreis_key FROM normalized.de_kreis WHERE geography_version = %s ORDER BY 1",
            (geography_version,),
        )
    ]


def regionalreport_rows(
    path: Path, kreis_key: str, file_date: date,
    contract: BaRegionalreportContract = BA_REGIONALREPORT_CONTRACT,
) -> Iterator[tuple]:
    sha256 = sha256_file(path)
    url = contract.url(kreis_key, file_date)
    for row in read_regionalreport(path, kreis_key, file_date, contract):
        yield (
            file_date, kreis_key, row.table_name, row.sheet_row, row.region_label,
            row.row_code, row.row_label, row.quarter_date, row.quarter_label, row.value,
            str(path), sha256, url,
        )


def wz_rows(
    path: Path, region_code: str, file_date: date,
    contract: BaWzHeftContract = BA_WZ_HEFT_CONTRACT,
) -> Iterator[tuple]:
    booklet = read_wz_heft(path, file_date, contract)
    sha256 = sha256_file(path)
    url = contract.url(region_code, file_date)
    for row in booklet.rows:
        yield (
            file_date, contract.regions[region_code], booklet.region_name, row.sheet_row,
            row.wz_code, row.wz_label, list(row.values), str(path), sha256, url,
        )


def load_regionalreport_raw(
    file_date: date,
    geography_version: str,
    project_root: Path = Path("."),
) -> RawLoadResult:
    """Load the Kreis files of one quarter into raw (replacing that quarter)."""
    with database_connection(application_name="lwr_germany_sector_load") as connection:
        apply_sector_schema(connection)
        keys = kreis_keys(connection, geography_version)
        connection.execute("DELETE FROM raw.de_ba_regionalreport WHERE file_date = %s", (file_date,))
        loaded = copy_rows(
            connection, schema="raw", table="de_ba_regionalreport",
            columns=REGIONALREPORT_COLUMNS,
            rows=(
                row
                for key in keys
                for row in regionalreport_rows(
                    project_root / BA_REGIONALREPORT_CONTRACT.path(key, file_date), key, file_date
                )
            ),
        )
        connection.execute("ANALYZE raw.de_ba_regionalreport")
    return RawLoadResult("raw.de_ba_regionalreport", str(file_date), f"{len(keys)} files",
                         loaded.row_count, loaded.duration_seconds)


def load_wz_heft_raw(file_date: date, project_root: Path = Path(".")) -> RawLoadResult:
    """Load the 17 WZ booklets of one quarter into raw (replacing that quarter)."""
    with database_connection(application_name="lwr_germany_sector_load") as connection:
        apply_sector_schema(connection)
        connection.execute("DELETE FROM raw.de_ba_wz_heft WHERE file_date = %s", (file_date,))
        loaded = copy_rows(
            connection, schema="raw", table="de_ba_wz_heft", columns=WZ_COLUMNS,
            rows=(
                row
                for code in BA_WZ_HEFT_CONTRACT.regions
                for row in wz_rows(project_root / BA_WZ_HEFT_CONTRACT.path(code, file_date), code, file_date)
            ),
        )
        connection.execute("ANALYZE raw.de_ba_wz_heft")
    return RawLoadResult("raw.de_ba_wz_heft", str(file_date), "17 files",
                         loaded.row_count, loaded.duration_seconds)


def main(argv: list[str] | None = None) -> int:
    from src.germany.contract import GEOGRAPHY_VERSION

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file-date", type=date.fromisoformat, required=True)
    parser.add_argument("--pause-seconds", type=float, default=1.5)
    parser.add_argument("--wz-only", action="store_true", help="Download the WZ booklets only.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with database_connection(application_name="lwr_germany_sector_keys") as connection:
        keys = kreis_keys(connection, GEOGRAPHY_VERSION)
    started = time.monotonic()
    with requests.Session() as session:
        client = PoliteClient(session, pause_seconds=args.pause_seconds)
        booklets = acquire_wz_hefte(client, args.file_date)
        reports = [] if args.wz_only else acquire_regionalreports(client, args.file_date, keys)
    print(json.dumps({
        "file_date": args.file_date.isoformat(),
        "wz_files": len(booklets), "wz_downloaded": sum(b.downloaded for b in booklets),
        "kreis_files": len(reports), "kreis_downloaded": sum(r.downloaded for r in reports),
        "requests": client.requests_sent,
        "bytes": sum(item.size_bytes for item in [*booklets, *reports]),
        "seconds": round(time.monotonic() - started, 1),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
