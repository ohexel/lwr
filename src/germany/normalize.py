"""Build the normalized Germany hierarchy and population in PostGIS."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from psycopg import Connection

from src.database.connection import database_connection
from src.germany.contract import GEOGRAPHY_VERSION, VG250_CONTRACT, ZENSUS_CONTRACT
from src.germany.raw_load import apply_germany_schema


NORMALIZED_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "germany" / "normalized_schema.sql"
)


def zensus_source_sha256s() -> list[str]:
    return [extract.csv_sha256 for extract in ZENSUS_CONTRACT.extracts]


def apply_normalized_schema(connection: Connection) -> None:
    apply_germany_schema(connection)
    connection.execute(NORMALIZED_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class HierarchySummary:
    land_count: int
    kreis_count: int
    gemeinde_count: int
    unincorporated_count: int


@dataclass(frozen=True)
class PopulationSummary:
    gemeinde_count: int
    census_value_count: int
    zero_count: int
    unincorporated_without_record: int
    missing_record_count: int
    rejected_count: int
    published_row_count: int


def refresh_hierarchy(
    geography_version: str = GEOGRAPHY_VERSION,
) -> HierarchySummary:
    with database_connection(application_name="lwr_germany_hierarchy") as connection:
        apply_normalized_schema(connection)
        row = connection.execute(
            "SELECT * FROM normalized.refresh_de_hierarchy(%s, %s)",
            (VG250_CONTRACT.archive_sha256, geography_version),
        ).fetchone()
    if row is None:
        raise RuntimeError("refresh_de_hierarchy returned no summary")
    return HierarchySummary(*(int(value) for value in row))


def refresh_population(
    geography_version: str = GEOGRAPHY_VERSION,
) -> PopulationSummary:
    with database_connection(application_name="lwr_germany_population") as connection:
        apply_normalized_schema(connection)
        row = connection.execute(
            "SELECT * FROM normalized.refresh_de_population(%s, %s, %s)",
            (
                geography_version,
                zensus_source_sha256s(),
                ZENSUS_CONTRACT.reference_date,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("refresh_de_population returned no summary")
    return PopulationSummary(*(int(value) for value in row))
