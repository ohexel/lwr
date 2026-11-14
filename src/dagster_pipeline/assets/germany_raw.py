import dagster as dg
import requests

from src.germany.acquire import (
    acquire_vg250,
    acquire_zensus,
    extract_vg250_geopackage,
)
from src.germany.contract import VG250_CONTRACT, ZENSUS_CONTRACT
from src.germany.raw_load import load_vg250_raw, load_zensus_raw


GERMANY_GROUP = "germany"

RAW_DE_VG250_KEY = dg.AssetKey(["raw", "de_vg250"])
RAW_DE_ZENSUS2022_POPULATION_KEY = dg.AssetKey(["raw", "de_zensus2022_population"])


@dg.asset(
    key=RAW_DE_VG250_KEY,
    group_name=GERMANY_GROUP,
    description=(
        "BKG VG250 (Stand 31.12.2022) Staat, Land, Kreis and Gemeinde "
        "features as delivered, after checksum verification."
    ),
)
def raw_de_vg250(context: dg.AssetExecutionContext) -> None:
    with requests.Session() as session:
        archive = acquire_vg250(session)
    extract_vg250_geopackage(archive.path)
    results = load_vg250_raw()

    context.add_output_metadata(
        {
            "source_url": VG250_CONTRACT.source_url,
            "vintage": VG250_CONTRACT.vintage.isoformat(),
            "source_sha256": archive.sha256,
            "downloaded": archive.downloaded,
            **{
                f"rows_{result.target_table.split('[')[1].rstrip(']')}": (
                    result.row_count
                )
                for result in results
            },
            "load_duration_seconds": round(
                sum(result.duration_seconds for result in results), 2
            ),
        }
    )


@dg.asset(
    key=RAW_DE_ZENSUS2022_POPULATION_KEY,
    group_name=GERMANY_GROUP,
    description=(
        "Zensus 2022 table 1000A-1003 (age, 5 classes; Gebietsstand "
        "15.05.2022) for Länder, Kreise and Gemeinden, as delivered by "
        "the Zensus API after checksum verification."
    ),
)
def raw_de_zensus2022_population(context: dg.AssetExecutionContext) -> None:
    with requests.Session() as session:
        acquired = acquire_zensus(session)
    results = load_zensus_raw()

    context.add_output_metadata(
        {
            "table_code": ZENSUS_CONTRACT.table_code,
            "reference_date": ZENSUS_CONTRACT.reference_date.isoformat(),
            "downloaded_files": sum(item.downloaded for item in acquired),
            **{
                f"rows_{extract.regional_variable}": result.row_count
                for extract, result in zip(ZENSUS_CONTRACT.extracts, results)
            },
        }
    )


GERMANY_RAW_ASSETS = [
    raw_de_vg250,
    raw_de_zensus2022_population,
]
