"""Theme-independent indicators on the Germany hierarchy (ADR 019).

normalized.de_indicator_source and normalized.de_indicator hold the
catalogue, filled from the source contracts here; normalized.de_indicator_value
holds one row per region, level, indicator and reference date. The first
source is the BA Gemeindedaten; a further source adds its catalogue rows and
its own refresh function, the tables stay as they are.
"""

from dataclasses import dataclass
from datetime import date

from psycopg import Connection

from src.database.connection import database_connection
from src.germany.ba_gemeindedaten import apply_indicator_schema
from src.germany.contract import (
    BA_ADDITIVE_INDICATORS,
    BA_GEMEINDEDATEN_CONTRACT,
    BA_INDICATORS,
    GEOGRAPHY_VERSION,
)


BA_UPDATE_CYCLE = "jährlich, Stichtag 30. Juni; Veröffentlichung im Januar"
UNITS = {"establishments": "Betriebe"}


@dataclass(frozen=True)
class IndicatorRefreshSummary:
    reference_date: str
    geography_version: str
    # "<level>:<value_status>" -> rows
    rows: dict[str, int]


def upsert_ba_catalogue(connection: Connection) -> None:
    contract = BA_GEMEINDEDATEN_CONTRACT
    connection.execute(
        """
        INSERT INTO normalized.de_indicator_source
            (source_code, title, publisher, homepage, citation, terms, update_cycle)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_code) DO UPDATE SET
            title = EXCLUDED.title,
            publisher = EXCLUDED.publisher,
            homepage = EXCLUDED.homepage,
            citation = EXCLUDED.citation,
            terms = EXCLUDED.terms,
            update_cycle = EXCLUDED.update_cycle
        """,
        (
            contract.source_code,
            contract.title,
            contract.publisher,
            contract.homepage,
            contract.citation,
            contract.terms,
            BA_UPDATE_CYCLE,
        ),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO normalized.de_indicator
                (indicator_code, source_code, label_de, unit, source_column, is_additive)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (indicator_code) DO UPDATE SET
                source_code = EXCLUDED.source_code,
                label_de = EXCLUDED.label_de,
                unit = EXCLUDED.unit,
                source_column = EXCLUDED.source_column,
                is_additive = EXCLUDED.is_additive
            """,
            [
                (
                    code,
                    contract.source_code,
                    label,
                    UNITS.get(code, "Personen"),
                    column,
                    code in BA_ADDITIVE_INDICATORS,
                )
                for code, label, column in BA_INDICATORS
            ],
        )


def refresh_ba_indicators(
    reference_date: date,
    geography_version: str = GEOGRAPHY_VERSION,
) -> IndicatorRefreshSummary:
    with database_connection(application_name="lwr_germany_indicators") as connection:
        apply_indicator_schema(connection)
        upsert_ba_catalogue(connection)
        connection.execute("ANALYZE raw.de_ba_gemeindedaten")
        connection.execute("ANALYZE normalized.de_indicator")
        rows = connection.execute(
            "SELECT * FROM normalized.refresh_de_ba_indicators(%s, %s)",
            (reference_date, geography_version),
        ).fetchall()
    if not rows:
        raise RuntimeError("refresh_de_ba_indicators returned no summary")
    return IndicatorRefreshSummary(
        reference_date=reference_date.isoformat(),
        geography_version=geography_version,
        rows={f"{level}:{status}": int(count) for level, status, count in rows},
    )


def latest_loaded_reference_date(connection: Connection) -> date | None:
    row = connection.execute(
        "SELECT MAX(reference_date) FROM raw.de_ba_gemeindedaten"
    ).fetchone()
    return None if row is None else row[0]
