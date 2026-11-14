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


# ---------------------------------------------------------------------------
# Sectors (ADR 020): BA Regionalreport (Kreise) and BA WZ booklets (Länder)
# ---------------------------------------------------------------------------
SECTOR_UPDATE_CYCLE = "vierteljährlich; Veröffentlichung etwa sechs Monate nach dem Stichtag"
WZ_SUM_LABELS = {
    "wz_sum_c": ("Verarbeitendes Gewerbe (Summe der Wirtschaftsabteilungen 10 bis 33, eigene Berechnung)",
                 tuple(f"{n:02d}" for n in range(10, 34))),
}


def sector_row_indicators() -> list[str | None]:
    """Indicator per position of a row in Regionalreport table 2.3."""
    from src.germany.contract import BA_REGIONALREPORT_CONTRACT

    return [code for _, code, _ in BA_REGIONALREPORT_CONTRACT.sector_rows]


def upsert_sector_catalogue(connection: Connection) -> None:
    from src.germany.contract import (
        BA_REGIONALREPORT_CONTRACT,
        BA_WZ_HEFT_CONTRACT,
        SECTOR_GROUP_DIVISIONS,
    )

    terms = BA_GEMEINDEDATEN_CONTRACT.terms
    for contract in (BA_REGIONALREPORT_CONTRACT, BA_WZ_HEFT_CONTRACT):
        connection.execute(
            """
            INSERT INTO normalized.de_indicator_source
                (source_code, title, publisher, homepage, citation, terms, update_cycle)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_code) DO UPDATE SET
                title = EXCLUDED.title, publisher = EXCLUDED.publisher,
                homepage = EXCLUDED.homepage, citation = EXCLUDED.citation,
                terms = EXCLUDED.terms, update_cycle = EXCLUDED.update_cycle
            """,
            (contract.source_code, contract.title, BA_GEMEINDEDATEN_CONTRACT.publisher,
             BA_GEMEINDEDATEN_CONTRACT.homepage, contract.citation, terms, SECTOR_UPDATE_CYCLE),
        )
    rows: list[tuple] = [
        (code, BA_REGIONALREPORT_CONTRACT.source_code, label, "Personen", position, True)
        for position, (_, code, label) in enumerate(BA_REGIONALREPORT_CONTRACT.sector_rows, start=1)
        if code is not None
    ]
    # WZ divisions, group 24.1, total and "keine Angabe", labelled as delivered
    # in the newest booklet.
    for wz_code, label in connection.execute(
        """
        SELECT DISTINCT ON (wz_code) wz_code, wz_label
        FROM raw.de_ba_wz_heft
        WHERE wz_code ~ '^[0-9]{2}$' OR wz_code IN ('241', 'total', 'XX')
        ORDER BY wz_code, file_date DESC
        """
    ):
        code = {"total": "wz_total", "XX": "wz_unassigned"}.get(wz_code, f"wz_{wz_code}")
        rows.append((code, BA_WZ_HEFT_CONTRACT.source_code, label, "Personen", 1, True))
    components: list[tuple[str, str]] = []
    sums = dict(WZ_SUM_LABELS)
    for group, divisions in SECTOR_GROUP_DIVISIONS.items():
        label = next(l for _, c, l in BA_REGIONALREPORT_CONTRACT.sector_rows if c == group)
        sums[group.replace("sector_c_", "wz_sum_")] = (
            f"{label} (Summe der Wirtschaftsabteilungen {', '.join(divisions)}, eigene Berechnung)",
            divisions,
        )
    for code, (label, divisions) in sums.items():
        rows.append((code, BA_WZ_HEFT_CONTRACT.source_code, label, "Personen", None, True))
        components.extend((code, f"wz_{division}") for division in divisions)
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO normalized.de_indicator
                (indicator_code, source_code, label_de, unit, source_column, is_additive)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (indicator_code) DO UPDATE SET
                source_code = EXCLUDED.source_code, label_de = EXCLUDED.label_de,
                unit = EXCLUDED.unit, source_column = EXCLUDED.source_column,
                is_additive = EXCLUDED.is_additive
            """,
            rows,
        )
        known = {row[0] for row in rows}
        missing = sorted({c for _, c in components if c not in known})
        if missing:
            raise RuntimeError(f"WZ divisions missing from the booklets: {missing}")
        cursor.execute(
            "DELETE FROM normalized.de_indicator_sum_component WHERE derived_code LIKE 'wz_sum_%'"
        )
        cursor.executemany(
            "INSERT INTO normalized.de_indicator_sum_component VALUES (%s, %s)", components
        )


def upsert_sector_quality_notes(connection: Connection) -> None:
    """Reviewed differences in the BA's figures (contract) as quality notes:
    on the Kreis, and on its Land, whose Kreis sum is then not used."""
    from src.germany.contract import SECTOR_SOURCE_DISCREPANCIES

    connection.execute(
        "DELETE FROM normalized.de_indicator_quality_note WHERE source_code = 'ba_regionalreport'"
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO normalized.de_indicator_quality_note VALUES ('ba_regionalreport', %s, %s, %s, %s)",
            [
                (level, key, d.quarter, d.note_de)
                for d in SECTOR_SOURCE_DISCREPANCIES
                for level, key in (("krs", d.kreis_key), ("lan", d.land_key))
            ],
        )


def refresh_sectors(geography_version: str = GEOGRAPHY_VERSION) -> IndicatorRefreshSummary:
    from src.germany.ba_sectors import apply_sector_schema

    with database_connection(application_name="lwr_germany_sectors") as connection:
        apply_sector_schema(connection)
        upsert_sector_catalogue(connection)
        for table in ("raw.de_ba_regionalreport", "raw.de_ba_wz_heft", "normalized.de_indicator",
                      "normalized.de_indicator_sum_component"):
            connection.execute(f"ANALYZE {table}")
        rows = connection.execute(
            "SELECT * FROM normalized.refresh_de_ba_sectors(%s, %s)",
            (geography_version, sector_row_indicators()),
        ).fetchall()
        upsert_sector_quality_notes(connection)
        newest = connection.execute(
            "SELECT MAX(file_date) FROM raw.de_ba_regionalreport"
        ).fetchone()[0]
    if not rows:
        raise RuntimeError("refresh_de_ba_sectors returned no summary")
    return IndicatorRefreshSummary(
        reference_date=newest.isoformat() if newest else "",
        geography_version=geography_version,
        rows={f"{source}:{level}:{status}": int(count) for source, level, status, count in rows},
    )
