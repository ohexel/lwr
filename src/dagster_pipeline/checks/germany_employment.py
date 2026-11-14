"""Checks of the BA Gemeindedaten against the source and the hierarchy.

All checks evaluate the newest reference date in raw.de_ba_gemeindedaten,
which is the one the assets have just loaded.
"""

from collections import Counter
from datetime import date

import dagster as dg
from psycopg import Connection

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_employment import (
    NORMALIZED_DE_INDICATOR_VALUE_KEY,
    RAW_DE_BA_GEMEINDEDATEN_KEY,
)
from src.germany.contract import BA_GEMEINDEDATEN_CONTRACT, GEOGRAPHY_VERSION
from src.germany.indicators import latest_loaded_reference_date


MAX_LISTED = 50


def _reference_date(connection: Connection) -> date:
    reference_date = latest_loaded_reference_date(connection)
    if reference_date is None:
        raise RuntimeError("raw.de_ba_gemeindedaten is empty")
    return reference_date


@dg.asset_check(asset=RAW_DE_BA_GEMEINDEDATEN_KEY, name="de_ba_source_contract")
def de_ba_source_contract() -> dg.AssetCheckResult:
    """Row counts per sheet and level, value signs, and the pinned vintage."""
    with database_connection(application_name="lwr_germany_ba_check") as connection:
        reference_date = _reference_date(connection)
        counts = {
            f"{sheet}:{level}": int(count)
            for sheet, level, count in connection.execute(
                """
                SELECT sheet_name, source_level, COUNT(*)
                FROM raw.de_ba_gemeindedaten WHERE reference_date = %s
                GROUP BY 1, 2
                """,
                (reference_date,),
            )
        }
        hashes, bad_values, suppressed_above_gemeinde = connection.execute(
            """
            SELECT
                array_agg(DISTINCT r.source_sha256),
                COUNT(*) FILTER (WHERE l.source_value !~ '^([0-9]+|[*])$'),
                COUNT(*) FILTER (WHERE l.source_value = '*' AND l.source_level <> 'gem')
            FROM raw.de_ba_gemeindedaten AS r
            JOIN normalized.de_ba_long(%s) AS l
              ON l.sheet_name = r.sheet_name AND l.source_key = r.source_key
            WHERE r.reference_date = %s
            """,
            (reference_date, reference_date),
        ).fetchone()

    contract = BA_GEMEINDEDATEN_CONTRACT
    expected = {
        "Gemeindedaten:lan": contract.land_count,
        "Länder:lan": contract.land_count,
        "Länder:sta": 1,
        "Länder:unassigned": 1,
    }
    known = contract.vintage(reference_date)
    if known is not None:
        expected.update(
            {
                "Gemeindedaten:gem": known.gemeinde_rows,
                "Gemeindedaten:krs": known.kreis_rows,
                "Gemeindedaten:rb": known.regierungsbezirk_rows,
            }
        )
    failures = [
        f"{key}: {counts.get(key, 0)} rows, expected {value}"
        for key, value in expected.items()
        if counts.get(key, 0) != value
    ]
    if known is not None and hashes != [known.sha256]:
        failures.append(f"source checksums {hashes}, expected {known.sha256}")
    if bad_values:
        failures.append(f"{bad_values} values are neither digits nor '*'")
    if suppressed_above_gemeinde:
        failures.append(f"{suppressed_above_gemeinde} suppressed values above Gemeinde level")

    return dg.AssetCheckResult(
        passed=not failures,
        metadata={
            "reference_date": reference_date.isoformat(),
            "known_vintage": known is not None,
            "row_counts": counts,
            "failures": failures,
        },
        description=(
            "Both sheets as delivered: 16 Länder in each, one Germany and one "
            "unassigned row, only digits or '*' (suppressed), '*' only at "
            "Gemeinde level. A vintage pinned in the contract must also match "
            "its checksum and row counts."
        ),
    )


@dg.asset_check(asset=RAW_DE_BA_GEMEINDEDATEN_KEY, name="de_ba_column_identities")
def de_ba_column_identities() -> dg.AssetCheckResult:
    """Identities between the columns of every row, at every level."""
    with database_connection(application_name="lwr_germany_ba_check") as connection:
        reference_date = _reference_date(connection)
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_ba_identities(%s)", (reference_date,)
        ).fetchall()
    return dg.AssetCheckResult(
        passed=not rows,
        metadata={
            "reference_date": reference_date.isoformat(),
            "violation_count": len(rows),
            "violations_by_rule": dict(Counter(row[0] for row in rows)),
            "violations": [f"{r[0]}: {r[1]} {r[2]} {r[3]}" for r in rows[:MAX_LISTED]],
        },
        description=(
            "Wohnort = Männer + Frauen exactly; Deutsche + Ausländer <= Wohnort; "
            "Einpendler <= Arbeitsort - (Wohnort gleich Arbeitsort); "
            "Auspendler <= Wohnort - (Wohnort gleich Arbeitsort). Rows with a "
            "suppressed value in a rule are skipped for that rule."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_INDICATOR_VALUE_KEY, name="de_ba_key_coverage")
def de_ba_key_coverage() -> dg.AssetCheckResult:
    """BA Gemeinde keys against the hierarchy; documented exceptions only."""
    with database_connection(application_name="lwr_germany_ba_check") as connection:
        reference_date = _reference_date(connection)
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_ba_key_coverage(%s, %s)",
            (reference_date, GEOGRAPHY_VERSION),
        ).fetchall()

    known = BA_GEMEINDEDATEN_CONTRACT.vintage(reference_date)
    documented = {
        "source_without_geography": known.gemeinden_without_geography if known else frozenset(),
        "no_source_record": known.geography_without_source if known else frozenset(),
    }
    blocking, expected = [], []
    for issue, level, key, name in rows:
        line = f"{issue} {level} {key} {name}"
        if issue == "no_source_record_unincorporated":
            expected.append(line)
        elif level == "gem" and key in documented.get(issue, frozenset()):
            expected.append(line)
        else:
            blocking.append(line)
    missing_documented = sorted(
        f"{issue} {key}"
        for issue, keys in documented.items()
        for key in keys
        if not any(r[0] == issue and r[2] == key for r in rows)
    )
    return dg.AssetCheckResult(
        passed=not blocking and not missing_documented,
        metadata={
            "reference_date": reference_date.isoformat(),
            "geography_version": GEOGRAPHY_VERSION,
            "issue_counts": dict(Counter(row[0] for row in rows)),
            "blocking": blocking[:MAX_LISTED],
            "documented_exceptions_not_seen": missing_documented,
            "expected_exceptions": expected,
        },
        description=(
            "Lists every BA region without a counterpart in the hierarchy and "
            "every region of the hierarchy without a BA row. Expected: "
            "unincorporated areas (no employees reported) and the Gemeinden "
            "changed by mergers after the geography version, as listed in the "
            "contract for this vintage. Anything else, and every Kreis or "
            "Land, is blocking; a new vintage therefore needs its changes "
            "reviewed before this check passes."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_INDICATOR_VALUE_KEY, name="de_ba_source_totals")
def de_ba_source_totals() -> dg.AssetCheckResult:
    """Sums of additive indicators against the BA's published totals."""
    with database_connection(application_name="lwr_germany_ba_check") as connection:
        reference_date = _reference_date(connection)
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_ba_source_totals(%s)", (reference_date,)
        ).fetchall()

    failures: list[str] = []
    exact: Counter[str] = Counter()
    with_suppressed: Counter[str] = Counter()
    largest_gap: dict[str, float] = {}
    for comparison, key, indicator, published, summed, suppressed in rows:
        if suppressed == 0:
            if published != summed:
                failures.append(
                    f"{comparison} {key} {indicator}: published {published}, sum {summed}"
                )
            else:
                exact[comparison] += 1
        else:
            with_suppressed[comparison] += 1
            if published < summed:
                failures.append(
                    f"{comparison} {key} {indicator}: published {published} below "
                    f"the visible sum {summed}"
                )
            elif published:
                gap = (published - summed) / published
                largest_gap[indicator] = max(largest_gap.get(indicator, 0.0), gap)

    return dg.AssetCheckResult(
        passed=bool(rows) and not failures,
        metadata={
            "reference_date": reference_date.isoformat(),
            "comparisons": len(rows),
            "exact": dict(exact),
            "with_suppressed_parts": dict(with_suppressed),
            "largest_relative_gap_where_suppressed": {
                key: round(value, 4) for key, value in sorted(largest_gap.items())
            },
            "failures": failures[:MAX_LISTED],
        },
        description=(
            "Additive indicators only (employees by residence and its "
            "breakdowns, employees at place of work, establishments): "
            "Gemeinden sum exactly to their Kreis where nothing is suppressed, "
            "and never exceed it where something is; Kreise sum exactly to "
            "their Land; Länder plus the unassigned row sum exactly to Germany."
        ),
    )


GERMANY_EMPLOYMENT_CHECKS = [
    de_ba_source_contract,
    de_ba_column_identities,
    de_ba_key_coverage,
    de_ba_source_totals,
]
