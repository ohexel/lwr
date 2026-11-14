"""Checks of the sector data against the sources, across levels and against
the BA Gemeindedaten. All evaluate the newest Regionalreport file date."""

from collections import Counter
from datetime import date

import dagster as dg
from psycopg import Connection

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_sectors import (
    NORMALIZED_DE_SECTOR_VALUES_KEY,
    RAW_DE_BA_REGIONALREPORT_KEY,
)
from src.germany.contract import (
    BA_REGIONALREPORT_CONTRACT,
    BA_WZ_HEFT_CONTRACT,
    GEOGRAPHY_VERSION,
    LAND_DIVISIONS,
    MAX_UNASSIGNED_SHARE,
    SECTOR_SOURCE_DISCREPANCIES,
)


MAX_LISTED = 50


def _file_date(connection: Connection) -> date:
    row = connection.execute("SELECT MAX(file_date) FROM raw.de_ba_regionalreport").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("raw.de_ba_regionalreport is empty")
    return row[0]


@dg.asset_check(asset=RAW_DE_BA_REGIONALREPORT_KEY, name="de_ba_sector_source_contract")
def de_ba_sector_source_contract() -> dg.AssetCheckResult:
    """One complete file per Kreis and per Land; consistent Land rows."""
    contract = BA_REGIONALREPORT_CONTRACT
    rows_per_file = len(contract.sector_rows) * contract.quarters_per_file
    with database_connection(application_name="lwr_germany_sector_check") as connection:
        file_date = _file_date(connection)
        kreise, complete, land_variants = connection.execute(
            """
            WITH per_file AS (
                SELECT kreis_key, COUNT(*) FILTER (WHERE table_name = '2.3') AS rows_23
                FROM raw.de_ba_regionalreport WHERE file_date = %s GROUP BY kreis_key
            ),
            land AS (
                SELECT left(kreis_key, 2), quarter_date, COUNT(DISTINCT value) AS variants
                FROM raw.de_ba_regionalreport
                WHERE file_date = %s AND table_name = '2.1'
                  AND region_label LIKE 'Bundesland %%' AND row_code = 'Insgesamt'
                GROUP BY 1, 2
            )
            SELECT (SELECT COUNT(*) FROM per_file),
                   (SELECT COUNT(*) FROM per_file WHERE rows_23 = %s),
                   (SELECT COUNT(*) FROM land WHERE variants > 1)
            """,
            (file_date, file_date, rows_per_file),
        ).fetchone()
        hierarchy = connection.execute(
            "SELECT COUNT(*) FROM normalized.de_kreis WHERE geography_version = %s",
            (GEOGRAPHY_VERSION,),
        ).fetchone()[0]
        booklets = dict(
            connection.execute(
                """
                SELECT region_key, COUNT(*) FILTER (WHERE wz_code = ANY(%s))
                FROM raw.de_ba_wz_heft WHERE file_date = %s GROUP BY region_key
                """,
                (["total", *LAND_DIVISIONS], file_date),
            ).fetchall()
        )
    failures = []
    if kreise != hierarchy or complete != hierarchy:
        failures.append(f"{kreise} Kreis files, {complete} complete, hierarchy has {hierarchy}")
    if land_variants:
        failures.append(f"{land_variants} Land totals differ between Kreis files of one Land")
    expected_regions = set(BA_WZ_HEFT_CONTRACT.regions.values())
    if set(booklets) != expected_regions:
        failures.append(f"WZ booklets for {sorted(booklets)}, expected {sorted(expected_regions)}")
    incomplete = sorted(k for k, n in booklets.items() if n != 1 + len(LAND_DIVISIONS))
    if incomplete:
        failures.append(f"WZ booklets without total or the four divisions: {incomplete}")
    return dg.AssetCheckResult(
        passed=not failures,
        metadata={
            "file_date": file_date.isoformat(),
            "kreis_files": kreise,
            "wz_booklets": len(booklets),
            "failures": failures,
        },
        description=(
            "Every Kreis of the hierarchy has one Regionalreport with all rows of "
            "table 2.3 for five quarters (the parser already refuses any other "
            "layout); the Land totals in table 2.1 agree between the Kreis files "
            "of a Land; there is a WZ booklet for each Land and Germany with the "
            "total and the four divisions."
        ),
    )


@dg.asset_check(asset=RAW_DE_BA_REGIONALREPORT_KEY, name="de_ba_sector_identities")
def de_ba_sector_identities() -> dg.AssetCheckResult:
    """Sections, groups and sector rows of table 2.3 add up."""
    with database_connection(application_name="lwr_germany_sector_check") as connection:
        file_date = _file_date(connection)
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_ba_sector_identities(%s)", (file_date,)
        ).fetchall()
    return dg.AssetCheckResult(
        passed=not rows,
        metadata={
            "file_date": file_date.isoformat(),
            "violation_count": len(rows),
            "violations_by_rule": dict(Counter(row[0] for row in rows)),
            "violations": [f"{r[0]}: {r[1]} {r[2]} published {r[3]} sum {r[4]}" for r in rows[:MAX_LISTED]],
        },
        description=(
            "Per Kreis and quarter: total = sections + unassigned; C = its three "
            "groups; N = N without temporary agency work + temporary agency work; "
            "B-F and G-U = their sections; total = A + B-F + G-U + unassigned. "
            "Exact where no part is suppressed; otherwise the published value is "
            "at least the visible sum."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_SECTOR_VALUES_KEY, name="de_ba_sector_source_totals")
def de_ba_sector_source_totals() -> dg.AssetCheckResult:
    """Totals across Kreise, Länder, the two products and the Gemeindedaten."""
    with database_connection(application_name="lwr_germany_sector_check") as connection:
        file_date = _file_date(connection)
        rows = connection.execute(
            "SELECT * FROM normalized.check_de_ba_sector_totals(%s, %s)",
            (file_date, GEOGRAPHY_VERSION),
        ).fetchall()
    failures: list[str] = []
    exact: Counter[str] = Counter()
    bounded: Counter[str] = Counter()
    not_comparable: Counter[str] = Counter()
    documented: list[str] = []
    discrepancies = {(d.land_key, d.quarter): d for d in SECTOR_SOURCE_DISCREPANCIES}
    # Germany minus the Länder, per source and date: employees that cannot be
    # assigned to a Land (the cap for divisions).
    unassigned = {
        (comparison, quarter): published - summed
        for comparison, key, item, quarter, published, summed, suppressed in rows
        if comparison in ("laender_to_germany", "wz_laender_to_germany") and item == "total"
    }
    for comparison, key, item, quarter, published, summed, suppressed in rows:
        label = f"{comparison} {key} {item} {quarter}: published {published}, sum {summed}"
        if published is None:
            not_comparable[comparison] += 1
            continue
        documented_gap = discrepancies.get((key, quarter))
        if comparison in ("kreise_to_land", "kreise_group_vs_wz") and documented_gap:
            if comparison == "kreise_to_land" and published - summed != documented_gap.kreise_below_land:
                failures.append(f"{label}; documented gap {documented_gap.kreise_below_land}")
            else:
                documented.append(label)
            continue
        if comparison in ("laender_to_germany", "wz_laender_to_germany"):
            gap = published - summed
            cap = unassigned.get((comparison, quarter), 0) if item != "total" else published * MAX_UNASSIGNED_SHARE
            if suppressed and gap < 0 or not suppressed and not 0 <= gap <= cap:
                failures.append(f"{label}; unassigned gap must be within [0, {cap:.0f}]")
            else:
                (bounded if suppressed or gap else exact)[comparison] += 1
            continue
        if suppressed:
            bounded[comparison] += 1
            if published < summed:
                failures.append(f"{label}; published below the visible sum")
        elif published != summed:
            failures.append(label)
        else:
            exact[comparison] += 1
    return dg.AssetCheckResult(
        passed=not failures,
        metadata={
            "file_date": file_date.isoformat(),
            "exact": dict(exact),
            "within_bounds": dict(bounded),
            "not_comparable_both_sides_suppressed": dict(not_comparable),
            "germany_minus_laender": {f"{c} {q}": int(v) for (c, q), v in sorted(unassigned.items())},
            "documented_source_discrepancies": documented,
            "kreis_vs_gemeindedaten": exact.get("kreis_vs_gemeindedaten", 0),
            "failures": failures[:MAX_LISTED],
        },
        description=(
            "Σ Kreise = Land (Regionalreport); Land totals and manufacturing "
            "groups of the Regionalreport = the WZ booklets of the same quarter; "
            "WZ divisions + 'keine Angabe' = WZ total; Germany = Σ Länder plus "
            "employees not assignable to a Land (at most 0.01 %, equal to the "
            "Gemeindedaten's unassigned row where both have the date); Kreis "
            "totals = Gemeindedaten employees at place of work. Exact where "
            "nothing is suppressed, bounded otherwise. Differences in the BA's "
            "own figures that were reviewed are listed in the contract and "
            "accepted only with exactly the documented size."
        ),
    )


@dg.asset_check(asset=NORMALIZED_DE_SECTOR_VALUES_KEY, name="de_ba_sector_key_coverage")
def de_ba_sector_key_coverage() -> dg.AssetCheckResult:
    """Every Kreis, Land and Germany has values for the newest quarter."""
    with database_connection(application_name="lwr_germany_sector_check") as connection:
        rows = connection.execute(
            """
            SELECT level, region_key, region_name, employees_total_status
            FROM analytical.de_current_sectors
            WHERE employees_total_status <> 'value'
            ORDER BY 1, 2
            """
        ).fetchall()
        counts = dict(
            connection.execute(
                "SELECT level, COUNT(*) FROM analytical.de_current_sectors GROUP BY 1"
            ).fetchall()
        )
        names = connection.execute(
            """
            SELECT w.region_key, w.region_name, l.land_name
            FROM (SELECT DISTINCT region_key, region_name FROM raw.de_ba_wz_heft) AS w
            JOIN normalized.de_land AS l
              ON l.land_key = w.region_key AND l.geography_version = %s
            WHERE w.region_name <> l.land_name
            """,
            (GEOGRAPHY_VERSION,),
        ).fetchall()
    return dg.AssetCheckResult(
        passed=not rows and counts.get("krs") == 400 and counts.get("lan") == 16 and counts.get("sta") == 1,
        metadata={
            "regions_per_level": counts,
            "without_total": [f"{r[0]} {r[1]} {r[2]}: {r[3]}" for r in rows[:MAX_LISTED]],
            "wz_region_names_differing_from_vg250": [f"{r[0]}: {r[1]} / {r[2]}" for r in names],
        },
        description=(
            "All 400 Kreise, 16 Länder and Germany have a total for the newest "
            "quarter. Land names in the WZ booklets that differ from VG250 are "
            "listed for information."
        ),
    )


GERMANY_SECTOR_CHECKS = [
    de_ba_sector_source_contract,
    de_ba_sector_identities,
    de_ba_sector_source_totals,
    de_ba_sector_key_coverage,
]
