import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.database.connection import database_connection
from src.database.load import copy_rows


DEFAULT_POPULATION_PATH = Path(
    "data/raw/population/2025-12-31/"
    "EWR_L21_202512E_Matrix.csv"
)

AFS_POPULATION_URL = (
    "https://daten.berlin.de/datensaetze/"
    "einwohnerinnen-und-einwohner-in-berlin-in-"
    "lor-planungsraumen-am-31-12-2025"
)

AFS_PUBLISHER = "Amt für Statistik Berlin-Brandenburg"
AFS_REFERENCE_DATE = date(2025, 12, 31)
AFS_PUBLICATION_DATE = date(2026, 4, 2)

REQUIRED_COLUMNS = (
    "RAUMID",
    "E_E",
    "E_E65U80",
    "E_E80U110",
    "ZEIT",
)


@dataclass(frozen=True)
class AfsPopulationLoadResult:
    source_path: str
    source_sha256: str
    target_table: str
    row_count: int
    duration_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _read_source_rows(
    source_path: Path,
) -> list[dict[str, str | None]]:
    with source_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(
            handle,
            delimiter=";",
        )

        fieldnames = reader.fieldnames or []
        missing = [
            column
            for column in REQUIRED_COLUMNS
            if column not in fieldnames
        ]
        if missing:
            raise ValueError(
                "AfS population CSV is missing required columns: "
                + ", ".join(missing)
            )

        return [
            {
                column: _empty_to_none(row.get(column))
                for column in REQUIRED_COLUMNS
            }
            for row in reader
        ]


def load_afs_population_raw(
    source_path: Path = DEFAULT_POPULATION_PATH,
) -> AfsPopulationLoadResult:
    if not source_path.exists():
        raise FileNotFoundError(
            f"AfS population source not found: {source_path}"
        )

    source_sha256 = _sha256(source_path)
    source_rows = _read_source_rows(source_path)

    rows = [
        (
            row["RAUMID"],
            row["E_E"],
            row["E_E65U80"],
            row["E_E80U110"],
            row["ZEIT"],
            AFS_REFERENCE_DATE,
            AFS_PUBLICATION_DATE,
            str(source_path),
            source_sha256,
            AFS_POPULATION_URL,
            AFS_PUBLISHER,
        )
        for row in source_rows
    ]

    with database_connection(
        application_name="capstone_afs_population_load"
    ) as connection:
        # Re-loading the same retained source snapshot replaces only
        # rows carrying that exact source hash.
        connection.execute(
            """
            DELETE FROM raw.afs_population
            WHERE source_sha256 = %s
            """,
            (source_sha256,),
        )

        result = copy_rows(
            connection,
            schema="raw",
            table="afs_population",
            columns=(
                "plr_id_source",
                "population_total_source",
                "population_65_79_source",
                "population_80plus_source",
                "reference_code_source",
                "reference_date",
                "publication_date",
                "source_path",
                "source_sha256",
                "source_url",
                "publisher",
            ),
            rows=rows,
        )

    return AfsPopulationLoadResult(
        source_path=str(source_path),
        source_sha256=source_sha256,
        target_table=result.target_table,
        row_count=result.row_count,
        duration_seconds=result.duration_seconds,
    )
