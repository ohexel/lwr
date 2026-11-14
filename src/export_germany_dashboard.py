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

GEMEINDE_QUERY = """
SELECT s.ars, s.gemeinde_name, s.kreis_key, s.land_key,
       s.max_temperature_c, s.max_temperature_at_berlin,
       s.max_apparent_temperature_shade_c, s.max_apparent_temperature_shade_at_berlin,
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
               max_temperature_area_weighted_c, max_temperature_area_weighted_lead_hour,
               max_temperature_population_weighted_c, max_temperature_population_weighted_lead_hour,
               max_apparent_temperature_shade_area_weighted_c, max_apparent_temperature_shade_area_weighted_lead_hour,
               max_apparent_temperature_shade_population_weighted_c, max_apparent_temperature_shade_population_weighted_lead_hour,
               population_total, gemeinde_count
        FROM analytical.de_current_kreis_summary
        ORDER BY kreis_key
    """,
    "lan": """
        SELECT land_key, land_name, NULL::text, lead_time_count,
               max_temperature_area_weighted_c, max_temperature_area_weighted_lead_hour,
               max_temperature_population_weighted_c, max_temperature_population_weighted_lead_hour,
               max_apparent_temperature_shade_area_weighted_c, max_apparent_temperature_shade_area_weighted_lead_hour,
               max_apparent_temperature_shade_population_weighted_c, max_apparent_temperature_shade_population_weighted_lead_hour,
               population_total, gemeinde_count
        FROM analytical.de_current_land_summary
        ORDER BY land_key
    """,
}

BUND_QUERY = """
SELECT max_apparent_temperature_shade_area_weighted_c, max_apparent_temperature_shade_area_weighted_lead_hour, max_apparent_temperature_shade_population_weighted_c, max_apparent_temperature_shade_population_weighted_lead_hour
FROM analytical.de_current_bund_summary
"""

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


# Order of the measures in SUMMARY_QUERIES (value, lead hour) pairs.
SUMMARY_FIELDS = (
    "max_temperature_area_weighted_c",
    "max_temperature_population_weighted_c",
    "max_apparent_temperature_shade_area_weighted_c",
    "max_apparent_temperature_shade_population_weighted_c",
)


def lead_hour_key(value_key: str) -> str:
    """Key of the lead hour that belongs to a maximum, e.g.
    max_temperature_area_weighted_c -> max_temperature_area_weighted_lead_hour."""
    return value_key.removesuffix("_c") + "_lead_hour"


def summary_records(rows: list[tuple]) -> dict[str, dict[str, object]]:
    """Per region: 24-hour maxima (and their lead hour) of both weightings,
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
    for (ars, name, kreis, land, t_max, t_at, a_max, a_at, population,
         status, bez) in connection.execute(GEMEINDE_QUERY):
        t_label, a_label = _berlin_label(t_at), _berlin_label(a_at)
        if t_label not in hour_of or a_label not in hour_of:
            raise ValueError(f"{ars}: time of maximum is outside the run")
        if t_max is None or a_max is None:
            raise ValueError(f"{ars}: missing forecast maximum")
        gemeinden[ars] = {
            "name": name,
            "kreis_key": kreis,
            "land_key": land,
            "max_temperature_c": _round(t_max),
            "max_temperature_lead_hour": hour_of[t_label],
            "max_apparent_temperature_shade_c": _round(a_max),
            "max_apparent_temperature_shade_lead_hour": hour_of[a_label],
            "population_total": population,
            "population_status": status,
            "gemeinde_type": bez,
        }

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

    bund_a_area, bund_a_area_h, bund_a_pop, bund_a_pop_h = connection.execute(
        BUND_QUERY
    ).fetchone()
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
        "bund": {
            "max_apparent_temperature_shade_area_weighted_c": _round(bund_a_area),
            "max_apparent_temperature_shade_area_weighted_lead_hour": int(bund_a_area_h),
            "max_apparent_temperature_shade_population_weighted_c": _round(bund_a_pop),
            "max_apparent_temperature_shade_population_weighted_lead_hour": int(bund_a_pop_h),
            "population_total": leads[0][7],
        },
        "lan": laender,
        "krs": kreise,
        "gem": gemeinden,
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
