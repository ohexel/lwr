"""Germany layer, second theme: employment per Gemeinde (BA Gemeindedaten).

The reference date is run configuration: the release sensor requests a run
for a new yearly vintage; without configuration the newest vintage known to
the contract is loaded.
"""

from dataclasses import asdict
from datetime import date

import dagster as dg
import requests

from src.dagster_pipeline.assets.germany_normalized import NORMALIZED_DE_HIERARCHY_KEY
from src.dagster_pipeline.assets.germany_raw import GERMANY_GROUP
from src.germany.ba_gemeindedaten import (
    acquire_ba_gemeindedaten,
    load_ba_gemeindedaten_raw,
)
from src.germany.contract import BA_GEMEINDEDATEN_CONTRACT, GEOGRAPHY_VERSION
from src.germany.indicators import refresh_ba_indicators


RAW_DE_BA_GEMEINDEDATEN_KEY = dg.AssetKey(["raw", "de_ba_gemeindedaten"])
NORMALIZED_DE_INDICATOR_VALUE_KEY = dg.AssetKey(["normalized", "de_indicator_value"])


class BaGemeindedatenConfig(dg.Config):
    reference_date: str = BA_GEMEINDEDATEN_CONTRACT.latest_known.isoformat()


@dg.asset(
    key=RAW_DE_BA_GEMEINDEDATEN_KEY,
    group_name=GERMANY_GROUP,
    description=(
        "BA Gemeindedaten (employees by place of residence and of work, "
        "commuters, establishments; yearly, 30 June) as delivered, after the "
        "structural contract and, for a known vintage, the checksum."
    ),
)
def raw_de_ba_gemeindedaten(
    context: dg.AssetExecutionContext, config: BaGemeindedatenConfig
) -> None:
    reference_date = date.fromisoformat(config.reference_date)
    with requests.Session() as session:
        acquired = acquire_ba_gemeindedaten(session, reference_date)
    result = load_ba_gemeindedaten_raw(reference_date)
    context.add_output_metadata(
        {
            "reference_date": reference_date.isoformat(),
            "source_url": BA_GEMEINDEDATEN_CONTRACT.url(reference_date),
            "source_sha256": acquired.sha256,
            "downloaded": acquired.downloaded,
            "known_vintage": BA_GEMEINDEDATEN_CONTRACT.vintage(reference_date) is not None,
            "rows": result.row_count,
            "load_duration_seconds": round(result.duration_seconds, 2),
        }
    )


@dg.asset(
    key=NORMALIZED_DE_INDICATOR_VALUE_KEY,
    deps=[RAW_DE_BA_GEMEINDEDATEN_KEY, NORMALIZED_DE_HIERARCHY_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "normalized.de_indicator_value: BA values on the Germany hierarchy, "
        "one row per region, level, indicator and reference date, with an "
        "explicit status (value, suppressed, not_reported). Served by "
        "analytical.de_current_employment."
    ),
)
def normalized_de_indicator_value(
    context: dg.AssetExecutionContext, config: BaGemeindedatenConfig
) -> None:
    summary = refresh_ba_indicators(
        date.fromisoformat(config.reference_date), GEOGRAPHY_VERSION
    )
    context.add_output_metadata(asdict(summary))


GERMANY_EMPLOYMENT_ASSETS = [
    raw_de_ba_gemeindedaten,
    normalized_de_indicator_value,
]
