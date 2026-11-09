import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_raw import (
    RAW_DE_VG250_KEY,
    RAW_DE_ZENSUS2022_AGE_KEY,
)
from src.germany.contract import VG250_CONTRACT, ZENSUS_CONTRACT


@dg.asset_check(asset=RAW_DE_VG250_KEY, name="de_vg250_source_contract")
def de_vg250_source_contract() -> dg.AssetCheckResult:
    """Per level: delivered and GF = 4 feature counts, unique ARS, validity."""
    with database_connection(application_name="lwr_germany_vg250_check") as connection:
        rows = connection.execute(
            """
            SELECT
                level,
                COUNT(*),
                COUNT(*) FILTER (WHERE gf = 4),
                COUNT(DISTINCT ars) FILTER (WHERE gf = 4),
                COUNT(*) FILTER (
                    WHERE geometry_source IS NULL
                       OR NOT ST_IsValid(geometry_source)
                )
            FROM raw.de_vg250
            WHERE source_sha256 = %s
            GROUP BY level
            """,
            (VG250_CONTRACT.archive_sha256,),
        ).fetchall()

    observed = {row[0]: row[1:] for row in rows}
    failures: list[str] = []
    for level, (total, land) in VG250_CONTRACT.feature_counts.items():
        got = observed.get(level)
        if got is None:
            failures.append(f"{level}: no rows")
            continue
        got_total, got_land, got_unique, got_invalid = got
        if got_total != total:
            failures.append(f"{level}: {got_total} features, expected {total}")
        if got_land != land or got_unique != land:
            failures.append(
                f"{level}: {got_land} GF=4 features / {got_unique} distinct "
                f"ARS, expected {land} each"
            )
        if got_invalid:
            failures.append(f"{level}: {got_invalid} missing/invalid geometries")

    return dg.AssetCheckResult(
        passed=not failures,
        metadata={
            "observed": {
                level: {
                    "features": int(values[0]),
                    "gf4_features": int(values[1]),
                    "gf4_distinct_ars": int(values[2]),
                    "invalid_geometries": int(values[3]),
                }
                for level, values in observed.items()
            },
            "failures": failures,
        },
        description=(
            "VG250 raw rows reproduce the contracted feature counts per "
            "level; GF = 4 holds exactly one record per ARS (BKG VG250 "
            "documentation, attribute GF)."
        ),
    )


@dg.asset_check(
    asset=RAW_DE_ZENSUS2022_AGE_KEY,
    name="de_zensus2022_age_source_contract",
)
def de_zensus2022_age_source_contract() -> dg.AssetCheckResult:
    """Per extract: region count, all five age classes, Germany total rows."""
    expected_hashes = {
        extract.csv_sha256: extract for extract in ZENSUS_CONTRACT.extracts
    }
    with database_connection(application_name="lwr_germany_zensus_check") as connection:
        rows = connection.execute(
            """
            SELECT
                source_sha256,
                COUNT(DISTINCT variable_1_attribute_code) FILTER (
                    WHERE variable_1_code <> 'GEODL1'
                ),
                COUNT(DISTINCT variable_1_attribute_code) FILTER (
                    WHERE variable_2_attribute_code = %s
                      AND value_unit = 'Anzahl'
                      AND variable_1_code <> 'GEODL1'
                ),
                COUNT(DISTINCT variable_2_attribute_code) FILTER (
                    WHERE variable_2_attribute_code IS NOT NULL
                ),
                COUNT(*) FILTER (WHERE variable_1_code = 'GEODL1'),
                MIN(time_value),
                MAX(time_value)
            FROM raw.de_zensus2022_age
            WHERE source_sha256 = ANY(%s)
            GROUP BY source_sha256
            """,
            (ZENSUS_CONTRACT.age_65plus_code, list(expected_hashes)),
        ).fetchall()

    observed = {row[0]: row[1:] for row in rows}
    failures: list[str] = []
    metadata: dict[str, dict] = {}
    for sha256, extract in expected_hashes.items():
        got = observed.get(sha256)
        name = extract.regional_variable
        if got is None:
            failures.append(f"{name}: not loaded")
            continue
        regions, regions_65, classes, germany_rows, first, last = got
        metadata[name] = {
            "regions": int(regions),
            "regions_with_65plus_count": int(regions_65),
            "age_classes": int(classes),
            "germany_rows": int(germany_rows),
        }
        if regions != extract.region_count or regions_65 != extract.region_count:
            failures.append(
                f"{name}: {regions} regions / {regions_65} with a 65+ count, "
                f"expected {extract.region_count}"
            )
        if classes != len(ZENSUS_CONTRACT.age_class_codes):
            failures.append(f"{name}: {classes} age classes, expected 5")
        if germany_rows == 0:
            failures.append(f"{name}: no Germany (GEODL1) rows")
        reference = ZENSUS_CONTRACT.reference_date.isoformat()
        if first != reference or last != reference:
            failures.append(f"{name}: reference date {first}..{last}, expected {reference}")

    return dg.AssetCheckResult(
        passed=not failures,
        metadata={"observed": metadata, "failures": failures},
        description=(
            "Each Zensus extract covers the contracted number of regions, "
            "every region has a 65+ count, all five age classes exist, the "
            "Germany total is present and the reference date is 2022-05-15."
        ),
    )


GERMANY_RAW_CHECKS = [
    de_vg250_source_contract,
    de_zensus2022_age_source_contract,
]
