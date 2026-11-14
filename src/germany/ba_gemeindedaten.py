"""BA Gemeindedaten: employees by place of residence and place of work,
commuters and establishments per Gemeinde, Kreis and Land (yearly, 30 June).

Usage:
    uv run --env-file .env python -m src.germany.ba_gemeindedaten [--reference-date 2025-06-30]

The file is a public xlsx download. A vintage listed in the contract is
verified against its checksum; a newer vintage is accepted when the workbook
passes the structural contract below (sheets, column layout, reference date
in the imprint, keys and value signs). Values stay text in raw: '*' marks a
value the BA suppressed (Geheimhaltung).
"""

import argparse
import json
import logging
import re
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
    BA_GEMEINDEDATEN_CONTRACT,
    BaGemeindedatenContract,
)
from src.germany.raw_load import RawLoadResult


LOGGER = logging.getLogger("germany_ba_gemeindedaten")
TIMEOUT_SECONDS = 120
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VALUE_PATTERN = re.compile(r"^[0-9]+$")
# Source level by key length in the sheet "Gemeindedaten".
LEVEL_BY_KEY_LENGTH = {8: "gem", 5: "krs", 3: "rb", 2: "lan"}
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "germany" / "indicator_schema.sql"
)
RAW_COLUMNS: tuple[str, ...] = (
    "sheet_name",
    "sheet_row",
    "source_level",
    "source_key",
    "source_name",
    *(f"column_{number:02d}" for number in range(1, 14)),
    "reference_date",
    "source_path",
    "source_sha256",
    "source_url",
    "publisher",
    "license",
)


@dataclass(frozen=True)
class BaRow:
    sheet_name: str
    sheet_row: int
    source_level: str
    source_key: str
    source_name: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class BaWorkbook:
    reference_date: date
    rows: tuple[BaRow, ...]

    def count(self, sheet_name: str, level: str) -> int:
        return sum(
            1 for row in self.rows
            if row.sheet_name == sheet_name and row.source_level == level
        )


class BaContractError(ValueError):
    """The workbook does not have the layout the pipeline was built for."""


# ---------------------------------------------------------------------------
# Availability and download
# ---------------------------------------------------------------------------
def vintage_is_published(
    session: requests.Session,
    reference_date: date,
    contract: BaGemeindedatenContract = BA_GEMEINDEDATEN_CONTRACT,
) -> bool:
    """HEAD request: 200 with an xlsx content type means the file exists."""
    response = session.head(
        contract.url(reference_date), allow_redirects=True, timeout=TIMEOUT_SECONDS
    )
    return (
        response.status_code == 200
        and response.headers.get("Content-Type", "").startswith(XLSX_CONTENT_TYPE)
    )


def next_reference_date(reference_date: date) -> date:
    return date(reference_date.year + 1, reference_date.month, reference_date.day)


def acquire_ba_gemeindedaten(
    session: requests.Session,
    reference_date: date,
    project_root: Path = Path("."),
    contract: BaGemeindedatenContract = BA_GEMEINDEDATEN_CONTRACT,
) -> AcquiredFile:
    target = project_root / contract.path(reference_date)
    known = contract.vintage(reference_date)

    if target.exists():
        existing = sha256_file(target)
        if known is not None and existing != known.sha256:
            raise RuntimeError(
                f"Existing BA file {target} has SHA-256 {existing}, expected "
                f"{known.sha256}. Refusing to use or overwrite it."
            )
        LOGGER.info("BA Gemeindedaten %s already present: %s", reference_date, target)
        return AcquiredFile(target, existing, target.stat().st_size, False)

    url = contract.url(reference_date)
    LOGGER.info("Downloading BA Gemeindedaten %s from %s", reference_date, url)
    response = session.get(url, timeout=TIMEOUT_SECONDS)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not content_type.startswith(XLSX_CONTENT_TYPE):
        raise RuntimeError(
            f"BA Gemeindedaten {reference_date} not available: HTTP "
            f"{response.status_code}, Content-Type {content_type!r}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".xlsx.part")
    partial.write_bytes(response.content)
    actual = sha256_file(partial)
    size = partial.stat().st_size
    if known is not None and (actual != known.sha256 or size != known.size_bytes):
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"BA Gemeindedaten {reference_date} does not match its contract: "
            f"sha256={actual} size={size}, expected sha256={known.sha256} "
            f"size={known.size_bytes}"
        )
    try:
        read_workbook(partial, reference_date, contract)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    partial.replace(target)
    target.with_suffix(".xlsx.acquisition.json").write_text(
        json.dumps(
            {
                "source_url": url,
                "reference_date": reference_date.isoformat(),
                "sha256": actual,
                "size_bytes": size,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "terms": contract.terms,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return AcquiredFile(target, actual, size, True)


# ---------------------------------------------------------------------------
# Workbook contract and parsing
# ---------------------------------------------------------------------------
def _collapse(text: object) -> str:
    return " ".join(str(text).split()) if text is not None else ""


def _cell_value(value: object, where: str, suppressed_sign: str) -> str:
    """A delivered value as text: digits, or the suppression sign."""
    if isinstance(value, bool):
        raise BaContractError(f"{where}: unexpected boolean {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise BaContractError(f"{where}: negative value {value}")
        return str(value)
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return str(int(value))
    text = str(value).strip() if value is not None else ""
    if text == suppressed_sign or VALUE_PATTERN.match(text):
        return text
    raise BaContractError(f"{where}: unexpected value {value!r}")


def _check_layout(sheet, sheet_name: str, contract: BaGemeindedatenContract) -> None:
    header_rows = list(
        sheet.iter_rows(min_row=1, max_row=contract.first_data_row - 2, values_only=True)
    )
    header_text = _collapse(" ".join(_collapse(c) for row in header_rows for c in row))
    missing = [f for f in contract.header_fragments if f not in header_text]
    if missing:
        raise BaContractError(f"{sheet_name}: header lacks {missing}")
    numbers = header_rows[-1][2:2 + len(contract.column_numbers)]
    if tuple(numbers) != contract.column_numbers:
        raise BaContractError(
            f"{sheet_name}: column numbers {numbers}, expected "
            f"{contract.column_numbers}"
        )


def _imprint_reference_date(workbook, contract: BaGemeindedatenContract) -> date:
    for row in workbook[contract.imprint_sheet].iter_rows(values_only=True):
        cells = [c for c in row if c is not None]
        if len(cells) >= 2 and _collapse(cells[0]) == "Stichtag:":
            value = cells[1]
            if isinstance(value, datetime):
                return value.date()
            raise BaContractError(f"Imprint reference date is {value!r}")
    raise BaContractError("Imprint has no 'Stichtag:' row")


def _data_rows(
    sheet, sheet_name: str, contract: BaGemeindedatenContract
) -> Iterator[BaRow]:
    width = len(contract.column_numbers)
    for row_number, row in enumerate(
        sheet.iter_rows(min_row=contract.first_data_row, values_only=True),
        start=contract.first_data_row,
    ):
        key = _collapse(row[0]) if row else ""
        name = _collapse(row[1]) if len(row) > 1 else ""
        if not key and not name:
            continue
        if key.startswith("©"):
            continue
        if key.isdigit():
            level = LEVEL_BY_KEY_LENGTH.get(len(key))
            if level is None:
                raise BaContractError(f"{sheet_name} row {row_number}: key {key!r}")
        elif key == contract.unassigned_label:
            level, key = "unassigned", "unassigned"
        elif not key and name == contract.germany_label:
            level, key = "sta", "DG"
        else:
            raise BaContractError(
                f"{sheet_name} row {row_number}: unexpected row {key!r} {name!r}"
            )
        cells = row[2:2 + width]
        if len(cells) < width:
            raise BaContractError(f"{sheet_name} row {row_number}: {len(cells)} values")
        values = tuple(
            _cell_value(
                value,
                f"{sheet_name} row {row_number} column {number}",
                contract.suppressed_sign,
            )
            for number, value in zip(contract.column_numbers, cells)
        )
        yield BaRow(sheet_name, row_number, level, key, name, values)


def read_workbook(
    path: Path,
    reference_date: date,
    contract: BaGemeindedatenContract = BA_GEMEINDEDATEN_CONTRACT,
) -> BaWorkbook:
    """Read both data sheets; raise BaContractError on any layout change."""
    # A file handle, not the path: openpyxl rejects names not ending in .xlsx
    # (the download is validated as <name>.xlsx.part before it is renamed).
    with path.open("rb") as handle:
        workbook = openpyxl.load_workbook(handle, read_only=True, data_only=True)
        try:
            rows = _read_rows(workbook, reference_date, contract)
        finally:
            workbook.close()
    content = BaWorkbook(reference_date, tuple(rows))
    _check_content(content, contract)
    return content


def _read_rows(
    workbook, reference_date: date, contract: BaGemeindedatenContract
) -> list[BaRow]:
    for sheet_name in (contract.data_sheet, contract.laender_sheet, contract.imprint_sheet):
        if sheet_name not in workbook.sheetnames:
            raise BaContractError(f"Sheet {sheet_name!r} missing: {workbook.sheetnames}")
    delivered_date = _imprint_reference_date(workbook, contract)
    if delivered_date != reference_date:
        raise BaContractError(
            f"Imprint reference date {delivered_date}, expected {reference_date}"
        )
    rows: list[BaRow] = []
    for sheet_name in (contract.data_sheet, contract.laender_sheet):
        sheet = workbook[sheet_name]
        _check_layout(sheet, sheet_name, contract)
        rows.extend(_data_rows(sheet, sheet_name, contract))
    return rows


def _check_content(content: BaWorkbook, contract: BaGemeindedatenContract) -> None:
    data, laender = contract.data_sheet, contract.laender_sheet
    if content.count(data, "lan") != 16 or content.count(laender, "lan") != 16:
        raise BaContractError("Both sheets must list the 16 Länder")
    if content.count(laender, "sta") != 1 or content.count(laender, "unassigned") != 1:
        raise BaContractError(
            "Sheet Länder must have one Germany and one unassigned row"
        )
    if content.count(data, "sta") or content.count(data, "unassigned"):
        raise BaContractError("Sheet Gemeindedaten must not have Germany rows")
    keys = [(row.sheet_name, row.source_key) for row in content.rows]
    if len(keys) != len(set(keys)):
        raise BaContractError("Duplicate keys within a sheet")
    land_rows = {
        sheet: {row.source_key: row.values for row in content.rows
                if row.sheet_name == sheet and row.source_level == "lan"}
        for sheet in (data, laender)
    }
    if land_rows[data] != land_rows[laender]:
        raise BaContractError("Land rows differ between the two sheets")
    for row in content.rows:
        if row.source_level != "gem" and contract.suppressed_sign in row.values:
            raise BaContractError(
                f"{row.sheet_name} row {row.sheet_row}: suppressed value above "
                "Gemeinde level"
            )


# ---------------------------------------------------------------------------
# Raw load
# ---------------------------------------------------------------------------
def apply_indicator_schema(connection: Connection) -> None:
    """Germany raw and normalized DDL first (the indicator tables reference the
    hierarchy), then the additive indicator DDL."""
    from src.germany.normalize import apply_normalized_schema

    apply_normalized_schema(connection)
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def raw_rows(
    content: BaWorkbook,
    *,
    path: Path,
    sha256: str,
    contract: BaGemeindedatenContract = BA_GEMEINDEDATEN_CONTRACT,
) -> Iterator[tuple[object, ...]]:
    url = contract.url(content.reference_date)
    for row in content.rows:
        yield (
            row.sheet_name,
            row.sheet_row,
            row.source_level,
            row.source_key,
            row.source_name,
            *row.values,
            content.reference_date,
            str(path),
            sha256,
            url,
            contract.publisher,
            contract.terms,
        )


def load_ba_gemeindedaten_raw(
    reference_date: date,
    project_root: Path = Path("."),
    contract: BaGemeindedatenContract = BA_GEMEINDEDATEN_CONTRACT,
) -> RawLoadResult:
    path = project_root / contract.path(reference_date)
    sha256 = sha256_file(path)
    known = contract.vintage(reference_date)
    if known is not None and sha256 != known.sha256:
        raise RuntimeError(f"{path} has SHA-256 {sha256}, contract expects {known.sha256}")
    content = read_workbook(path, reference_date, contract)

    with database_connection(application_name="lwr_germany_ba_load") as connection:
        apply_indicator_schema(connection)
        connection.execute(
            "DELETE FROM raw.de_ba_gemeindedaten WHERE reference_date = %s",
            (reference_date,),
        )
        loaded = copy_rows(
            connection,
            schema="raw",
            table="de_ba_gemeindedaten",
            columns=RAW_COLUMNS,
            rows=raw_rows(content, path=path, sha256=sha256, contract=contract),
        )
        # Fresh statistics: with the default estimate for a new table the
        # refresh joins about 11,000 x 11,000 rows per indicator.
        connection.execute("ANALYZE raw.de_ba_gemeindedaten")
    return RawLoadResult(
        target_table="raw.de_ba_gemeindedaten",
        source_path=str(path),
        source_sha256=sha256,
        row_count=loaded.row_count,
        duration_seconds=loaded.duration_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        default=BA_GEMEINDEDATEN_CONTRACT.latest_known,
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with requests.Session() as session:
        acquired = acquire_ba_gemeindedaten(session, args.reference_date)
    content = read_workbook(acquired.path, args.reference_date)
    print(
        json.dumps(
            {
                "path": str(acquired.path),
                "sha256": acquired.sha256,
                "downloaded": acquired.downloaded,
                "rows": {
                    f"{sheet}:{level}": content.count(sheet, level)
                    for sheet in (BA_GEMEINDEDATEN_CONTRACT.data_sheet,
                                  BA_GEMEINDEDATEN_CONTRACT.laender_sheet)
                    for level in ("gem", "krs", "rb", "lan", "sta", "unassigned")
                    if content.count(sheet, level)
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
