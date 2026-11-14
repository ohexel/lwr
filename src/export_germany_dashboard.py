"""Export the static Germany forecast map (MapLibre + PMTiles) and optionally
serve it locally.

    uv run --env-file .env python -m src.export_germany_dashboard --serve

Two kinds of data are written to <output>/data/:

* <geography_version>.pmtiles: Land, Kreis and Gemeinde outlines from
  normalized.de_* (VG250 31.12.2022), carrying only their regional key.
  Built once with tippecanoe; reused until the geography version changes
  or --rebuild-tiles is given. Never rebuilt per forecast run.
* values.json: the current complete forecast run (analytical.de_current_*),
  keyed by regional code and joined client-side.

The serving web host must answer HTTP Range requests (PMTiles); the local
preview (src/germany/preview_server.py) does.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import psycopg

from src.database.connection import database_connection
from src.germany.contract import GEOGRAPHY_VERSION, VG250_CONTRACT
from src.germany.preview_server import serve_dashboard


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_SOURCE = PROJECT_ROOT / "web" / "germany-dashboard"
PUBLIC_ASSETS = ("index.html", "styles.css", "app.js")
EXPECTED_LEAD_COUNT = 25
EXPECTED_COUNTS = {
    "lan": VG250_CONTRACT.feature_counts["lan"][1],
    "krs": VG250_CONTRACT.feature_counts["krs"][1],
    "gem": VG250_CONTRACT.feature_counts["gem"][1],
}
TILE_MIN_ZOOM = 4
TILE_MAX_ZOOM = 10

GEOMETRY_QUERIES = {
    "lan": """
        SELECT land_key, ST_AsGeoJSON(ST_Transform(geometry, 4326), 6)
        FROM normalized.de_land WHERE geography_version = %s ORDER BY 1
    """,
    "krs": """
        SELECT kreis_key, ST_AsGeoJSON(ST_Transform(geometry, 4326), 6)
        FROM normalized.de_kreis WHERE geography_version = %s ORDER BY 1
    """,
    "gem": """
        SELECT ars, ST_AsGeoJSON(ST_Transform(geometry, 4326), 6)
        FROM normalized.de_gemeinde WHERE geography_version = %s ORDER BY 1
    """,
}

LEADS_QUERY = """
SELECT run_time_utc, lead_hour, valid_time_utc,
       temperature_area_weighted_c, temperature_population_weighted_c,
       apparent_temperature_shade_area_weighted_c,
       apparent_temperature_shade_population_weighted_c,
       population_total
FROM analytical.de_current_bund_forecast_25h
ORDER BY lead_hour
"""

# 24-hour extremes served to the map: the maximum and the minimum of each
# measure, with the lead hour of its first occurrence.
EXTREMES = ("max", "min")
GEMEINDE_MEASURES = ("temperature", "apparent_temperature_shade")
WEIGHTED_MEASURES = (
    "temperature_area_weighted",
    "temperature_population_weighted",
    "apparent_temperature_shade_area_weighted",
    "apparent_temperature_shade_population_weighted",
)
# Order of the (value, lead hour) pairs in SUMMARY_QUERIES and of the
# (value, time) pairs in GEMEINDE_QUERY.
SUMMARY_FIELDS = tuple(f"{e}_{m}_c" for e in EXTREMES for m in WEIGHTED_MEASURES)
GEMEINDE_FIELDS = tuple(f"{e}_{m}_c" for e in EXTREMES for m in GEMEINDE_MEASURES)
BUND_FIELDS = tuple(
    f"{e}_apparent_temperature_shade_{w}_weighted_c"
    for e in EXTREMES for w in ("area", "population")
)


def lead_hour_key(value_key: str) -> str:
    """Key of the lead hour that belongs to an extreme, e.g.
    max_temperature_area_weighted_c -> max_temperature_area_weighted_lead_hour."""
    return value_key.removesuffix("_c") + "_lead_hour"


def _time_key(value_key: str) -> str:
    return value_key.removesuffix("_c") + "_at_berlin"


_SUMMARY_COLUMNS = ",\n               ".join(
    f"{field}, {lead_hour_key(field)}" for field in SUMMARY_FIELDS
)

GEMEINDE_QUERY = """
SELECT s.ars, s.gemeinde_name, s.kreis_key, s.land_key,
       """ + ", ".join(f"s.{f}, s.{_time_key(f)}" for f in GEMEINDE_FIELDS) + """,
       s.population_total, s.population_status, g.gemeinde_type
FROM analytical.de_current_gemeinde_summary AS s
JOIN normalized.de_gemeinde AS g
  ON g.ars = s.ars
 AND g.geography_version = (SELECT geography_version FROM analytical.de_current_run)
ORDER BY s.ars
"""

SUMMARY_QUERIES = {
    "krs": """
        SELECT kreis_key, kreis_name, land_key, lead_time_count,
               """ + _SUMMARY_COLUMNS + """,
               population_total, gemeinde_count
        FROM analytical.de_current_kreis_summary
        ORDER BY kreis_key
    """,
    "lan": """
        SELECT land_key, land_name, NULL::text, lead_time_count,
               """ + _SUMMARY_COLUMNS + """,
               population_total, gemeinde_count
        FROM analytical.de_current_land_summary
        ORDER BY land_key
    """,
}

BUND_QUERY = (
    "SELECT "
    + ", ".join(f"{field}, {lead_hour_key(field)}" for field in BUND_FIELDS)
    + " FROM analytical.de_current_bund_summary"
)

# Second theme: employment (BA Gemeindedaten), from analytical.de_current_employment.
EMPLOYMENT_MEASURES = (
    "employees_workplace",
    "employees_residence",
    "commuters_in",
    "commuters_out",
    "establishments",
)
EMPLOYMENT_QUERY = """
SELECT level, region_key, reference_date, geography_version,
       """ + ",\n       ".join(f"{m}, {m}_status" for m in EMPLOYMENT_MEASURES) + """,
       not_reported_reason, jobs_ratio, jobs_ratio_status
FROM analytical.de_current_employment
ORDER BY level, region_key
"""
JOBS_RATIO_STATUSES = frozenset({"value", "suppressed", "not_reported", "not_computable"})

# Third theme: employees by sector, from analytical.de_current_sectors.
SECTOR_MEASURES = (
    "employees_total",
    "manufacturing",
    "metal_electrical_steel",
    "intermediate_goods",
    "consumer_goods",
    "metal_electrical_steel_previous_year",
    "motor_vehicles",
    "machinery",
    "chemicals",
    "steel",
)
SECTOR_GROUPS = ("manufacturing", "metal_electrical_steel", "intermediate_goods", "consumer_goods")
SECTOR_QUERY = """
SELECT level, region_key, reference_date,
       """ + ",\n       ".join(f"{m}, {m}_status" for m in SECTOR_MEASURES) + """,
       metal_electrical_steel_share, metal_electrical_steel_share_status,
       """ + ", ".join(f"{g}_basis" for g in SECTOR_GROUPS) + """,
       quality_note
FROM analytical.de_current_sectors
ORDER BY level, region_key
"""
SECTOR_STATUSES = frozenset(
    {"value", "suppressed", "not_reported", "not_computable", "not_published_at_level"}
)

KREIS_TYPE_QUERY = """
SELECT kreis_key, kreis_type FROM normalized.de_kreis WHERE geography_version = %s
"""

# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
def _round(value: object, digits: int = 2) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite value {value!r}")
    return round(number, digits)


def _berlin_label(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M")


def summary_records(rows: list[tuple]) -> dict[str, dict[str, object]]:
    """Per region: 24-hour maxima and minima (and their lead hour) of both weightings,
    as computed by the analytical.de_current_*_summary views."""
    records: dict[str, dict[str, object]] = {}
    for key, name, parent, lead_count, *rest in rows:
        measures, population, count = rest[:-2], rest[-2], rest[-1]
        if lead_count != EXPECTED_LEAD_COUNT:
            raise ValueError(f"{key}: {lead_count} lead hours, expected {EXPECTED_LEAD_COUNT}")
        record: dict[str, object] = {
            "name": name,
            "population_total": population,
            "gemeinde_count": count,
        }
        if parent is not None:
            record["land_key"] = parent
        for field, value, hour in zip(SUMMARY_FIELDS, measures[0::2], measures[1::2]):
            if value is None:
                if "population_weighted" in field:
                    record[field] = None
                    record[lead_hour_key(field)] = None
                    continue
                raise ValueError(f"{key}: missing {field}")
            record[field] = _round(value)
            record[lead_hour_key(field)] = int(hour)
        records[key] = record
    return records


def collect_values(connection: psycopg.Connection) -> dict[str, object]:
    current = connection.execute(
        "SELECT geography_version, run_time_utc FROM analytical.de_current_run"
    ).fetchone()
    if current is None:
        raise RuntimeError("No complete Germany forecast run; run src.germany.run_forecast")
    geography_version, run_time = current

    leads = connection.execute(LEADS_QUERY).fetchall()
    if [row[1] for row in leads] != list(range(EXPECTED_LEAD_COUNT)):
        raise ValueError("Bund view does not have lead hours 0-24")
    valid_times = [row[2] for row in leads]
    berlin_times = [
        _berlin_label(v.astimezone(_berlin_zone())) for v in valid_times
    ]
    hour_of = {label: index for index, label in enumerate(berlin_times)}

    gemeinden: dict[str, dict[str, object]] = {}
    for ars, name, kreis, land, *rest in connection.execute(GEMEINDE_QUERY):
        extremes, (population, status, bez) = rest[:-3], rest[-3:]
        record: dict[str, object] = {"name": name, "kreis_key": kreis, "land_key": land}
        for field, value, at in zip(GEMEINDE_FIELDS, extremes[0::2], extremes[1::2]):
            if value is None or at is None:
                raise ValueError(f"{ars}: missing {field}")
            label = _berlin_label(at)
            if label not in hour_of:
                raise ValueError(f"{ars}: time of {field} is outside the run")
            record[field] = _round(value)
            record[lead_hour_key(field)] = hour_of[label]
        record.update(
            population_total=population, population_status=status, gemeinde_type=bez
        )
        gemeinden[ars] = record

    kreise = summary_records(connection.execute(SUMMARY_QUERIES["krs"]).fetchall())
    for key, kreis_type in connection.execute(KREIS_TYPE_QUERY, (geography_version,)):
        if key in kreise:
            kreise[key]["kreis_type"] = kreis_type
    laender = summary_records(connection.execute(SUMMARY_QUERIES["lan"]).fetchall())

    by_level = {"gem": gemeinden, "krs": kreise, "lan": laender}
    for level, records in by_level.items():
        if len(records) != EXPECTED_COUNTS[level]:
            raise ValueError(
                f"{level}: {len(records)} regions, expected {EXPECTED_COUNTS[level]}"
            )

    bund_row = connection.execute(BUND_QUERY).fetchone()
    bund: dict[str, object] = {}
    for field, value, hour in zip(BUND_FIELDS, bund_row[0::2], bund_row[1::2]):
        if value is None:
            raise ValueError(f"Bund: missing {field}")
        bund[field] = _round(value)
        bund[lead_hour_key(field)] = int(hour)
    bund["population_total"] = leads[0][7]
    all_apparent = [r["max_apparent_temperature_shade_c"] for r in gemeinden.values()]
    return {
        "schema": 2,
        "geography_version": geography_version,
        "tiles": f"{geography_version}.pmtiles",
        "run_time_utc": run_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "run_time_berlin": _berlin_label(run_time.astimezone(_berlin_zone())),
        "valid_times_berlin": berlin_times,
        "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "apparent_range_c": [
            math.floor(min(all_apparent)), math.ceil(max(all_apparent))
        ],
        "bund": bund,
        "lan": laender,
        "krs": kreise,
        "gem": gemeinden,
    }


def employment_record(row: tuple) -> dict[str, object]:
    """One region of employment.json. A measure is a number only when the
    source published it; otherwise it is null and "status" says why
    ("suppressed" by the BA or "not_reported")."""
    measures = row[4:4 + 2 * len(EMPLOYMENT_MEASURES)]
    reason, ratio, ratio_status = row[4 + 2 * len(EMPLOYMENT_MEASURES):]
    if ratio_status not in JOBS_RATIO_STATUSES:
        raise ValueError(f"{row[0]} {row[1]}: jobs_ratio_status {ratio_status!r}")
    if (ratio is not None) != (ratio_status == "value"):
        raise ValueError(f"{row[0]} {row[1]}: jobs_ratio {ratio!r} with {ratio_status}")
    record: dict[str, object] = {
        "jobs_ratio": _round(ratio, 4),
        "jobs_ratio_status": ratio_status,
    }
    status: dict[str, str] = {}
    for name, value, value_status in zip(EMPLOYMENT_MEASURES, measures[0::2], measures[1::2]):
        record[name] = int(value) if value_status == "value" else None
        if value_status != "value":
            status[name] = value_status
    if status:
        record["status"] = status
    if reason is not None:
        record["not_reported_reason"] = reason
    return record


def sector_record(row: tuple) -> dict[str, object]:
    """One region of sectors.json. A measure is a number only where the BA
    published it (or, for Land groups, all its parts); "status" says why
    another is null. Measures not published at the level are left out.
    "basis" says how a Land group was summed (own calculation)."""
    width = 2 * len(SECTOR_MEASURES)
    measures = row[3:3 + width]
    share, share_status = row[3 + width:5 + width]
    bases = row[5 + width:5 + width + len(SECTOR_GROUPS)]
    quality_note = row[5 + width + len(SECTOR_GROUPS)]
    record: dict[str, object] = {}
    basis = {group: value for group, value in zip(SECTOR_GROUPS, bases) if value}
    if basis:
        record["basis"] = basis
    if quality_note:
        record["quality_note"] = quality_note
    status: dict[str, str] = {}
    for name, value, value_status in zip(SECTOR_MEASURES, measures[0::2], measures[1::2]):
        if value_status not in SECTOR_STATUSES:
            raise ValueError(f"{row[0]} {row[1]} {name}: status {value_status!r}")
        if value_status == "not_published_at_level":
            continue
        record[name] = int(value) if value_status == "value" else None
        if value_status != "value":
            status[name] = value_status
    if share_status not in SECTOR_STATUSES or (share is not None) != (share_status == "value"):
        raise ValueError(f"{row[0]} {row[1]}: share {share!r} with {share_status}")
    record["metal_electrical_steel_share"] = _round(share, 5)
    record["metal_electrical_steel_share_status"] = share_status
    if status:
        record["status"] = status
    return record


def collect_sectors(connection: psycopg.Connection) -> dict[str, object] | None:
    """sectors.json, or None when the sector data is not loaded."""
    exists = connection.execute(
        "SELECT to_regclass('analytical.de_current_sectors') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return None
    rows = connection.execute(SECTOR_QUERY).fetchall()
    if not rows:
        return None
    by_level: dict[str, dict[str, dict[str, object]]] = {"sta": {}, "lan": {}, "krs": {}}
    dates: dict[str, set] = {"krs": set(), "lan": set()}
    for row in rows:
        by_level[row[0]][row[1]] = sector_record(row)
        dates["krs" if row[0] == "krs" else "lan"].add(row[2])
    for level in ("lan", "krs"):
        if len(by_level[level]) != EXPECTED_COUNTS[level]:
            raise ValueError(
                f"sectors {level}: {len(by_level[level])} regions, expected {EXPECTED_COUNTS[level]}"
            )
    if list(by_level["sta"]) != ["DG"] or len(dates["krs"]) != 1 or len(dates["lan"]) != 1:
        raise ValueError(f"sectors: unexpected Germany row or dates {dates}")
    sources = {
        code: {"title": title, "publisher": publisher, "citation": citation, "homepage": homepage}
        for code, title, publisher, citation, homepage in connection.execute(
            """
            SELECT source_code, title, publisher, citation, homepage
            FROM normalized.de_indicator_source
            WHERE source_code IN ('ba_regionalreport', 'ba_wz_heft')
            """
        )
    }
    return {
        "schema": 1,
        "theme": "sectors",
        "geography_version": GEOGRAPHY_VERSION,
        "kreis_reference_date": next(iter(dates["krs"])).isoformat(),
        "land_reference_date": next(iter(dates["lan"])).isoformat(),
        "sources": sources,
        "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "bund": by_level["sta"]["DG"],
        "lan": by_level["lan"],
        "krs": by_level["krs"],
    }


def collect_employment(connection: psycopg.Connection) -> dict[str, object] | None:
    """employment.json, or None when the BA data is not loaded."""
    exists = connection.execute(
        "SELECT to_regclass('analytical.de_current_employment') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return None
    rows = connection.execute(EMPLOYMENT_QUERY).fetchall()
    if not rows:
        return None
    reference_dates = {row[2] for row in rows}
    geography_versions = {row[3] for row in rows}
    if len(reference_dates) != 1 or geography_versions != {GEOGRAPHY_VERSION}:
        raise ValueError(
            f"employment view mixes {reference_dates} / {geography_versions}"
        )
    by_level: dict[str, dict[str, dict[str, object]]] = {
        "sta": {}, "lan": {}, "krs": {}, "gem": {}
    }
    for row in rows:
        by_level[row[0]][row[1]] = employment_record(row)
    for level in ("lan", "krs", "gem"):
        if len(by_level[level]) != EXPECTED_COUNTS[level]:
            raise ValueError(
                f"employment {level}: {len(by_level[level])} regions, "
                f"expected {EXPECTED_COUNTS[level]}"
            )
    if list(by_level["sta"]) != ["DG"]:
        raise ValueError("employment: expected exactly one Germany row")
    source = connection.execute(
        """
        SELECT title, publisher, citation, homepage
        FROM normalized.de_indicator_source WHERE source_code = 'ba_gemeindedaten'
        """
    ).fetchone()
    (reference_date,) = reference_dates
    return {
        "schema": 1,
        "theme": "employment",
        "geography_version": GEOGRAPHY_VERSION,
        "reference_date": reference_date.isoformat(),
        "source": {
            "title": source[0],
            "publisher": source[1],
            "citation": source[2],
            "homepage": source[3],
        },
        "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "bund": by_level["sta"]["DG"],
        "lan": by_level["lan"],
        "krs": by_level["krs"],
        "gem": by_level["gem"],
    }


def _berlin_zone():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------
def write_geometry_layers(
    connection: psycopg.Connection, directory: Path, geography_version: str
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for level, query in GEOMETRY_QUERIES.items():
        path = directory / f"{level}.geojsonl"
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for key, geometry in connection.execute(query, (geography_version,)):
                feature = {"type": "Feature", "properties": {"key": key},
                           "geometry": json.loads(geometry)}
                handle.write(json.dumps(feature, separators=(",", ":")) + "\n")
                count += 1
        if count != EXPECTED_COUNTS[level]:
            raise ValueError(f"{level}: {count} outlines, expected {EXPECTED_COUNTS[level]}")
        paths[level] = path
    return paths


def build_tiles(connection: psycopg.Connection, destination: Path,
                geography_version: str) -> dict[str, object]:
    tippecanoe = shutil.which("tippecanoe")
    if tippecanoe is None:
        raise RuntimeError(
            "tippecanoe is required to build the boundary tiles once "
            "(Debian/Ubuntu: apt install tippecanoe; macOS: brew install tippecanoe)"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lwr-germany-tiles-") as work:
        layers = write_geometry_layers(connection, Path(work), geography_version)
        output = Path(work) / destination.name
        command = [
            tippecanoe,
            "--output", str(output),
            "--force",
            "--quiet",
            f"--minimum-zoom={TILE_MIN_ZOOM}",
            f"--maximum-zoom={TILE_MAX_ZOOM}",
            # Choropleth: never drop or merge regions; neighbours keep shared
            # borders when simplified.
            "--no-feature-limit",
            "--no-tile-size-limit",
            "--detect-shared-borders",
            "--no-tiny-polygon-reduction",
            "--name", f"Germany administrative units {geography_version}",
            "--attribution",
            "© GeoBasis-DE / BKG 2022 (Daten verändert), dl-de/by-2-0",
        ]
        for level, path in layers.items():
            command += ["--named-layer", f"{level}:{path}"]
        version = subprocess.run([tippecanoe, "--version"], capture_output=True,
                                 text=True, check=False)
        subprocess.run(command, check=True)
        shutil.move(output, destination)
    return {
        "tiles_built": True,
        "tippecanoe": (version.stderr or version.stdout).strip(),
        "tiles_bytes": destination.stat().st_size,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def install_public_assets(destination: Path) -> None:
    if destination.resolve() == DASHBOARD_SOURCE.resolve():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for asset_name in PUBLIC_ASSETS:
        shutil.copy2(DASHBOARD_SOURCE / asset_name, destination / asset_name)


def export_germany_dashboard(
    *, output_dir: Path = DASHBOARD_SOURCE, rebuild_tiles: bool = False
) -> dict[str, object]:
    destination = output_dir.resolve()
    install_public_assets(destination)
    data_dir = destination / "data"
    result: dict[str, object] = {"status": "ready", "output_directory": str(destination)}

    with database_connection(application_name="lwr_germany_dashboard_export") as connection:
        values = collect_values(connection)
        employment = collect_employment(connection)
        sectors = collect_sectors(connection)
        if values["geography_version"] != GEOGRAPHY_VERSION:
            raise ValueError(
                f"current run uses {values['geography_version']}, "
                f"code expects {GEOGRAPHY_VERSION}"
            )
        tiles = data_dir / str(values["tiles"])
        if rebuild_tiles or not tiles.exists():
            result.update(build_tiles(connection, tiles, GEOGRAPHY_VERSION))
        else:
            result.update({"tiles_built": False, "tiles_bytes": tiles.stat().st_size})

    write_json_atomic(data_dir / "values.json", values)
    if employment is None:
        # The page then offers the temperature theme only.
        result["employment"] = "not loaded (python -m src.germany.run_employment)"
    else:
        write_json_atomic(data_dir / "employment.json", employment)
        result["employment"] = {
            "reference_date": employment["reference_date"],
            "bytes": (data_dir / "employment.json").stat().st_size,
        }
    if sectors is None:
        result["sectors"] = "not loaded (python -m src.germany.run_sectors)"
    else:
        write_json_atomic(data_dir / "sectors.json", sectors)
        result["sectors"] = {
            "kreis_reference_date": sectors["kreis_reference_date"],
            "land_reference_date": sectors["land_reference_date"],
            "bytes": (data_dir / "sectors.json").stat().st_size,
        }
    result.update(
        {
            "run_time_utc": values["run_time_utc"],
            "tiles": str(tiles.relative_to(destination)),
            "values_bytes": (data_dir / "values.json").stat().st_size,
            "regions": {level: len(values[level]) for level in ("lan", "krs", "gem")},
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export the static Germany forecast map (MapLibre, PMTiles, "
            "values JSON) and optionally serve it locally with Range support."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=DASHBOARD_SOURCE,
                        help="Standalone output root; defaults to web/germany-dashboard.")
    parser.add_argument("--rebuild-tiles", action="store_true",
                        help="Rebuild the boundary PMTiles even if they exist.")
    parser.add_argument("--serve", action="store_true", help="Start a local preview.")
    parser.add_argument("--host", default="127.0.0.1", help="Local preview address.")
    parser.add_argument("--port", type=int, default=8766, help="Local preview port.")
    arguments = parser.parse_args(argv)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        level=logging.INFO)
    try:
        result = export_germany_dashboard(
            output_dir=arguments.output_dir, rebuild_tiles=arguments.rebuild_tiles
        )
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            psycopg.Error) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    if arguments.serve:
        serve_dashboard(Path(result["output_directory"]), host=arguments.host,
                        port=arguments.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
