import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.database_population import (
    NORMALIZED_POPULATION_KEY,
    RAW_AFS_POPULATION_KEY,
    REJECTED_POPULATION_KEY,
)


@dg.asset_check(
    asset=NORMALIZED_POPULATION_KEY,
    additional_deps=[
        RAW_AFS_POPULATION_KEY,
        REJECTED_POPULATION_KEY,
    ],
    name="population_quality_accounting",
)
def population_quality_accounting(
    context: dg.AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    with database_connection(
        application_name="capstone_population_check"
    ) as connection:
        source = connection.execute(
            '''
            SELECT
                raw_population.source_sha256,
                MAX(raw_population.loaded_at_utc) AS latest_loaded_at
            FROM raw.afs_population AS raw_population
            GROUP BY raw_population.source_sha256
            ORDER BY latest_loaded_at DESC
            LIMIT 1
            '''
        ).fetchone()

        if source is None:
            return dg.AssetCheckResult(
                passed=False,
                description=(
                    "No raw AfS population source is available "
                    "for validation."
                ),
            )

        source_sha256 = str(source[0])

        result = connection.execute(
            '''
            SELECT
                quality.passed,
                quality.source_row_count,
                quality.accepted_row_count,
                quality.rejected_row_count,
                quality.accepted_rejected_overlap,
                quality.rejection_reasons
            FROM normalized.check_population_quality(%s)
                AS quality
            ''',
            (source_sha256,),
        ).fetchone()

    if result is None:
        return dg.AssetCheckResult(
            passed=False,
            description=(
                "Population quality SQL function returned no result."
            ),
        )

    return dg.AssetCheckResult(
        passed=bool(result[0]),
        metadata={
            "source_sha256": source_sha256,
            "source_row_count": int(result[1]),
            "accepted_row_count": int(result[2]),
            "rejected_row_count": int(result[3]),
            "accepted_rejected_overlap": int(result[4]),
            "rejection_reasons": result[5],
        },
        description=(
            "PostgreSQL validates that accepted + rejected "
            "population rows reconstruct the raw source and "
            "that the accepted/rejected sets do not overlap."
        ),
    )


POPULATION_CHECKS = [
    population_quality_accounting,
]
