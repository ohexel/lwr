from dataclasses import asdict

import dagster as dg

from src.dagster_pipeline.assets.germany_raw import (
    GERMANY_GROUP,
    RAW_DE_VG250_KEY,
    RAW_DE_ZENSUS2022_AGE_KEY,
)
from src.germany.contract import GEOGRAPHY_VERSION
from src.germany.normalize import refresh_hierarchy, refresh_population


NORMALIZED_DE_HIERARCHY_KEY = dg.AssetKey(["normalized", "de_hierarchy"])
NORMALIZED_DE_POPULATION_KEY = dg.AssetKey(["normalized", "de_population"])


@dg.asset(
    key=NORMALIZED_DE_HIERARCHY_KEY,
    deps=[RAW_DE_VG250_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "normalized.de_land, de_kreis and de_gemeinde: VG250 land parts "
        "(GF = 4) with parent keys, EPSG:3035 geometry and area."
    ),
)
def normalized_de_hierarchy(context: dg.AssetExecutionContext) -> None:
    summary = refresh_hierarchy()
    context.add_output_metadata(
        {"geography_version": GEOGRAPHY_VERSION, **asdict(summary)}
    )


@dg.asset(
    key=NORMALIZED_DE_POPULATION_KEY,
    deps=[NORMALIZED_DE_HIERARCHY_KEY, RAW_DE_ZENSUS2022_AGE_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "normalized.de_gemeinde_population (Zensus 2022 total population "
        "per Gemeinde; NULL with an explicit status, never 0, when there is "
        "no census record) and de_zensus_published_population (published "
        "Bund/Land/Kreis/Gemeinde totals for source checks)."
    ),
)
def normalized_de_population(context: dg.AssetExecutionContext) -> None:
    summary = refresh_population()
    context.add_output_metadata(
        {"geography_version": GEOGRAPHY_VERSION, **asdict(summary)}
    )


GERMANY_NORMALIZED_ASSETS = [
    normalized_de_hierarchy,
    normalized_de_population,
]
