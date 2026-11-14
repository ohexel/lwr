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
# Check 3: published Germany total minus the sum of the Länder in the pinned
# Zensus 2022 extracts (82,719,540 - 82,711,282). Destatis' Qualitätsbericht
# Zensus 2022 (20/11/2024, sections 1.1 and 7.2) counts Bundeswehr, police and
# foreign-service staff working abroad, with their families, in the population
# and includes them only at Bund level; the report does not state their number.
EXPECTED_BUND_MINUS_LAENDER = 8_258


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
    """Check 2: what foreign keys cannot guarantee."""
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
            "Foreign keys guarantee that every parent exists. Checked here: no "
            "Kreis without Gemeinden, no Land without Kreise, each Gemeinde's "
            "Land equals its Kreis's Land, Gemeinde keys agree with VG250 key "
            "parts, and Gemeinde areas sum to their Kreis area."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_POPULATION_KEY, name="de_source_totals")
def de_source_totals() -> dg.AssetCheckResult:
    """Check 3: Gemeinde population sums equal the published Zensus totals."""
    with database_connection(application_name="lwr_germany_check3") as connection:
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_source_totals(%s, %s)",
            (GEOGRAPHY_VERSION, zensus_source_sha256s()),
        ).fetchall()

    by_level: dict[str, list[tuple]] = {"krs": [], "lan": [], "sta": []}
    for row in rows:
        by_level.setdefault(row[0], []).append(row)
    exact_mismatches = [
        r for level in ("krs", "lan") for r in by_level[level] if r[5] != 0
    ]
    bund = by_level["sta"]
    bund_difference = bund[0][5] if len(bund) == 1 else None
    passed = (
        len(by_level["krs"]) > 0
        and len(by_level["lan"]) > 0
        and not exact_mismatches
        and bund_difference == EXPECTED_BUND_MINUS_LAENDER
    )
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "kreise_compared": len(by_level["krs"]),
            "laender_compared": len(by_level["lan"]),
            "kreis_land_mismatches": [
                f"{r[0]} {r[1]} {r[2]}: Σ Gemeinden {r[3]} vs published {r[4]}"
                for r in exact_mismatches[:MAX_LISTED]
            ],
            "kreis_land_mismatch_count": len(exact_mismatches),
            "bund_gemeinde_sum": None if not bund else bund[0][3],
            "bund_published_total": None if not bund else bund[0][4],
            "bund_difference": bund_difference,
            "bund_expected_difference": EXPECTED_BUND_MINUS_LAENDER,
        },
        description=(
            "Σ Gemeinde population equals the published Kreis and Land totals "
            "exactly. The published Germany total exceeds Σ Länder by the "
            "documented 8,258 (staff of Bundeswehr, police and foreign service "
            "abroad and their families are counted at Bund level only; "
            "Destatis Qualitätsbericht Zensus 2022, 1.1 and 7.2); any other "
            "difference fails."
        ),
    )


GERMANY_NORMALIZED_CHECKS = [
    de_key_coverage,
    de_hierarchy_integrity,
    de_source_totals,
]
