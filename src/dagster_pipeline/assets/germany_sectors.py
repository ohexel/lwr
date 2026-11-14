"""Germany layer, third theme: employees by sector (ADR 020).

Kreise from the BA Regionalreport (one file per Kreis), Länder and Germany
from the BA WZ booklets. The file date (quarter) is run configuration; the
release sensor requests a run for each new quarter.
"""

from dataclasses import asdict
from datetime import date

import dagster as dg
import requests

from src.dagster_pipeline.assets.germany_normalized import NORMALIZED_DE_HIERARCHY_KEY
from src.dagster_pipeline.assets.germany_raw import GERMANY_GROUP
from src.database.connection import database_connection
from src.germany.ba_sectors import (
    PoliteClient,
    acquire_regionalreports,
    acquire_wz_hefte,
    kreis_keys,
    load_regionalreport_raw,
    load_wz_heft_raw,
)
from src.germany.contract import GEOGRAPHY_VERSION, SECTOR_LATEST_KNOWN
from src.germany.indicators import refresh_sectors


RAW_DE_BA_REGIONALREPORT_KEY = dg.AssetKey(["raw", "de_ba_regionalreport"])
RAW_DE_BA_WZ_HEFT_KEY = dg.AssetKey(["raw", "de_ba_wz_heft"])
NORMALIZED_DE_SECTOR_VALUES_KEY = dg.AssetKey(["normalized", "de_sector_values"])


class SectorConfig(dg.Config):
    file_date: str = SECTOR_LATEST_KNOWN.isoformat()
    pause_seconds: float = 3.0


@dg.asset(
    key=RAW_DE_BA_WZ_HEFT_KEY,
    group_name=GERMANY_GROUP,
    description=(
        "BA Beschäftigte nach Wirtschaftszweigen (WZ 2008), sheet SVB - Tabelle I, "
        "for the 16 Länder and Germany, for the configured quarter and the same "
        "quarter a year earlier (year-on-year change), as delivered."
    ),
)
def raw_de_ba_wz_heft(context: dg.AssetExecutionContext, config: SectorConfig) -> None:
    file_date = date.fromisoformat(config.file_date)
    previous = date(file_date.year - 1, file_date.month, file_date.day)
    metadata: dict[str, object] = {}
    with requests.Session() as session:
        client = PoliteClient(session, pause_seconds=config.pause_seconds)
        for day in (previous, file_date):
            files = acquire_wz_hefte(client, day)
            result = load_wz_heft_raw(day)
            metadata[f"{day.isoformat()}_downloaded"] = sum(f.downloaded for f in files)
            metadata[f"{day.isoformat()}_rows"] = result.row_count
        metadata["requests"] = client.requests_sent
    context.add_output_metadata(metadata)


@dg.asset(
    key=RAW_DE_BA_REGIONALREPORT_KEY,
    deps=[NORMALIZED_DE_HIERARCHY_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "BA Regionalreport über Beschäftigte, tables 2.1 and 2.3, one file per "
        "Kreis of the hierarchy for the configured quarter (five quarters per "
        "file), downloaded one at a time, as delivered."
    ),
)
def raw_de_ba_regionalreport(context: dg.AssetExecutionContext, config: SectorConfig) -> None:
    file_date = date.fromisoformat(config.file_date)
    with database_connection(application_name="lwr_germany_sector_keys") as connection:
        keys = kreis_keys(connection, GEOGRAPHY_VERSION)
    with requests.Session() as session:
        client = PoliteClient(session, pause_seconds=config.pause_seconds)
        files = acquire_regionalreports(client, file_date, keys)
    result = load_regionalreport_raw(file_date, GEOGRAPHY_VERSION)
    context.add_output_metadata(
        {
            "file_date": file_date.isoformat(),
            "kreis_files": len(files),
            "downloaded": sum(f.downloaded for f in files),
            "requests": client.requests_sent,
            "bytes": sum(f.size_bytes for f in files),
            "rows": result.row_count,
        }
    )


@dg.asset(
    key=NORMALIZED_DE_SECTOR_VALUES_KEY,
    deps=[RAW_DE_BA_REGIONALREPORT_KEY, RAW_DE_BA_WZ_HEFT_KEY, NORMALIZED_DE_HIERARCHY_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "Sector rows of normalized.de_indicator_value: Kreise (Regionalreport, "
        "all quarters, newest file per quarter) and Länder and Germany (WZ "
        "booklets), each value with its status. Served by "
        "analytical.de_current_sectors."
    ),
)
def normalized_de_sector_values(context: dg.AssetExecutionContext) -> None:
    context.add_output_metadata(asdict(refresh_sectors(GEOGRAPHY_VERSION)))


GERMANY_SECTOR_ASSETS = [
    raw_de_ba_wz_heft,
    raw_de_ba_regionalreport,
    normalized_de_sector_values,
]
