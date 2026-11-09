import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_normalized import (
    NORMALIZED_DE_HIERARCHY_KEY,
    NORMALIZED_DE_POPULATION_KEY,
)
from src.germany.contract import GEOGRAPHY_VERSION
from src.germany.normalize import zensus_source_sha256s

# Gemeinden partition their Kreis in VG250; observed max relative area
# difference is ~5e-15 (floating point). 1e-9 leaves room for rounding only.
AREA_PARTITION_TOLERANCE = 1e-9
MAX_LISTED = 50


@dg.asset_check(asset=NORMALIZED_DE_POPULATION_KEY, name="de_key_coverage")
def de_key_coverage() -> dg.AssetCheckResult:
    """Check 1: every VG250 unit has a Zensus record and vice versa."""
    with database_connection(application_name="lwr_germany_key_check") as connection:
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_key_coverage(%s, %s)",
            (GEOGRAPHY_VERSION, zensus_source_sha256s()),
        ).fetchall()

    blocking = [row for row in rows if row[4]]
    summary: dict[str, int] = {}
    for level, issue, *_ in rows:
        summary[f"{level}:{issue}"] = summary.get(f"{level}:{issue}", 0) + 1

    return dg.AssetCheckResult(
        passed=not blocking,
        metadata={
            "exception_counts": summary,
            "blocking_exceptions": [
                f"{r[0]} {r[2]} {r[3]}: {r[1]}" for r in blocking[:MAX_LISTED]
            ],
            "non_blocking_exceptions": [
                f"{r[0]} {r[2]} {r[3]}: {r[1]}"
                for r in rows
                if not r[4]
            ],
        },
        description=(
            "Lists every Land/Kreis/Gemeinde key present in only one source. "
            "Unincorporated areas (gemeindefreie Gebiete) without a census "
            "record are expected and non-blocking; their population is NULL."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_HIERARCHY_KEY, name="de_hierarchy_integrity")
def de_hierarchy_integrity() -> dg.AssetCheckResult:
    """Check 2: one Kreis and one Land per Gemeinde, all parents exist."""
    with database_connection(application_name="lwr_germany_hierarchy_check") as connection:
        violations = connection.execute(
            "SELECT * FROM normalized.check_de_hierarchy(%s)",
            (GEOGRAPHY_VERSION,),
        ).fetchall()
        area = connection.execute(
            """
            SELECT COUNT(*),
                   MAX(ABS(relative_difference)),
                   COUNT(*) FILTER (WHERE ABS(relative_difference) > %s)
            FROM normalized.de_kreis_area_partition(%s)
            """,
            (AREA_PARTITION_TOLERANCE, GEOGRAPHY_VERSION),
        ).fetchone()

    kreise, max_relative, over_tolerance = area
    passed = not violations and kreise > 0 and over_tolerance == 0
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "violations": [f"{v[0]} {v[1]} ({v[2]})" for v in violations[:MAX_LISTED]],
            "violation_count": len(violations),
            "kreise_checked_for_area_partition": int(kreise),
            "max_relative_area_difference": float(max_relative or 0.0),
            "area_partition_tolerance": AREA_PARTITION_TOLERANCE,
            "kreise_over_tolerance": int(over_tolerance),
        },
        description=(
            "Every Gemeinde references an existing Kreis and Land, every Kreis "
            "an existing Land, no parent is empty, Gemeinde keys agree with "
            "VG250 key parts, and Gemeinde areas sum to their Kreis area."
        ),
    )


GERMANY_NORMALIZED_CHECKS = [
    de_key_coverage,
    de_hierarchy_integrity,
]
