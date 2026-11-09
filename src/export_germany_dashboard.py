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
preview server below does.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import psycopg

from src.database.connection import database_connection
from src.germany.contract import GEOGRAPHY_VERSION, VG250_CONTRACT


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

ROLLUP_QUERIES = {
    "krs": """
        SELECT kreis_key, kreis_name, land_key, lead_hour,
               temperature_area_weighted_c, temperature_population_weighted_c,
               apparent_temperature_shade_area_weighted_c,
               apparent_temperature_shade_population_weighted_c,
               population_total, gemeinde_count
        FROM analytical.de_current_kreis_forecast_25h
        ORDER BY kreis_key, lead_hour
    """,
    "lan": """
        SELECT land_key, land_name, NULL::text, lead_hour,
               temperature_area_weighted_c, temperature_population_weighted_c,
               apparent_temperature_shade_area_weighted_c,
               apparent_temperature_shade_population_weighted_c,
               population_total, gemeinde_count
        FROM analytical.de_current_land_forecast_25h
        ORDER BY land_key, lead_hour
    """,
}

KREIS_TYPE_QUERY = """
SELECT kreis_key, kreis_type FROM normalized.de_kreis WHERE geography_version = %s
"""

# A point inside each region (for search: the map zooms there).
ANCHOR_QUERY = """
SELECT level, key,
       round(ST_X(p)::numeric, 3)::float8, round(ST_Y(p)::numeric, 3)::float8
FROM (
    SELECT 'lan' AS level, land_key AS key,
           ST_Transform(ST_PointOnSurface(geometry), 4326) AS p
    FROM normalized.de_land WHERE geography_version = %(v)s
    UNION ALL
    SELECT 'krs', kreis_key, ST_Transform(ST_PointOnSurface(geometry), 4326)
    FROM normalized.de_kreis WHERE geography_version = %(v)s
    UNION ALL
    SELECT 'gem', ars, ST_Transform(ST_PointOnSurface(geometry), 4326)
    FROM normalized.de_gemeinde WHERE geography_version = %(v)s
) AS anchors
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


def _argmax(values: list[float]) -> int:
    # First lead hour with the maximum, matching de_current_gemeinde_summary.
    best = 0
    for index, value in enumerate(values):
        if value > values[best]:
            best = index
    return best


def _berlin_label(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M")


def rollup_records(rows: list[tuple]) -> dict[str, dict[str, object]]:
    """Per region: 24-hour maxima (and their lead hour) of both weightings."""
    series: dict[str, dict[str, object]] = {}
    for key, name, parent, lead, t_area, t_pop, a_area, a_pop, population, count in rows:
        entry = series.setdefault(
            key,
            {"name": name, "parent": parent, "population": population,
             "gemeinden": count, "t_area": [], "t_pop": [], "a_area": [], "a_pop": [],
             "leads": []},
        )
        entry["leads"].append(lead)
        for field, value in (("t_area", t_area), ("t_pop", t_pop),
                             ("a_area", a_area), ("a_pop", a_pop)):
            entry[field].append(value)

    records: dict[str, dict[str, object]] = {}
    for key, entry in series.items():
        if entry["leads"] != list(range(EXPECTED_LEAD_COUNT)):
            raise ValueError(f"{key}: lead hours {entry['leads']} are incomplete")
        record: dict[str, object] = {
            "n": entry["name"],
            "p": entry["population"],
            "g": entry["gemeinden"],
        }
        if entry["parent"] is not None:
            record["l"] = entry["parent"]
        for field in ("t_area", "t_pop", "a_area", "a_pop"):
            values = entry[field]
            if any(v is None for v in values):
                if field.endswith("_pop") and all(v is None for v in values):
                    record[field] = None
                    record[field + "_h"] = None
                    continue
                raise ValueError(f"{key}: missing {field}")
            hour = _argmax(values)
            record[field] = _round(values[hour])
            record[field + "_h"] = hour
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
            "n": name,
            "k": kreis,
            "l": land,
            "t": _round(t_max),
            "t_h": hour_of[t_label],
            "a": _round(a_max),
            "a_h": hour_of[a_label],
            "p": population,
            "s": status,
            "b": bez,
        }

    kreise = rollup_records(connection.execute(ROLLUP_QUERIES["krs"]).fetchall())
    for key, kreis_type in connection.execute(KREIS_TYPE_QUERY, (geography_version,)):
        if key in kreise:
            kreise[key]["b"] = kreis_type
    laender = rollup_records(connection.execute(ROLLUP_QUERIES["lan"]).fetchall())

    by_level = {"gem": gemeinden, "krs": kreise, "lan": laender}
    for level, key, lon, lat in connection.execute(ANCHOR_QUERY, {"v": geography_version}):
        if key in by_level[level]:
            by_level[level][key]["c"] = [lon, lat]

    for level, records in by_level.items():
        if any("c" not in record for record in records.values()):
            raise ValueError(f"{level}: region without anchor point")
        if len(records) != EXPECTED_COUNTS[level]:
            raise ValueError(
                f"{level}: {len(records)} regions, expected {EXPECTED_COUNTS[level]}"
            )

    bund_a_area = [row[5] for row in leads]
    bund_a_pop = [row[6] for row in leads]
    all_apparent = [r["a"] for r in gemeinden.values()]
    return {
        "schema": 1,
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
            "a_area": _round(max(bund_a_area)),
            "a_area_h": _argmax(bund_a_area),
            "a_pop": _round(max(bund_a_pop)),
            "a_pop_h": _argmax(bund_a_pop),
            "p": leads[0][7],
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
                feature = {"type": "Feature", "properties": {"k": key},
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


# ---------------------------------------------------------------------------
# Local preview with HTTP Range support (PMTiles reads byte ranges)
# ---------------------------------------------------------------------------
RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Return the inclusive (start, end) of a single byte range, or None if
    the header is not a satisfiable single range."""
    match = RANGE_PATTERN.match(header.strip())
    if not match or size == 0:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix range: last N bytes
        length = int(last)
        if length == 0:
            return None
        return max(size - length, 0), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start > end or start >= size:
        return None
    return start, end


class RangeRequestHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: D401 - stdlib override
        header = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if header is None or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        byte_range = parse_range(header, size)
        if byte_range is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        start, end = byte_range
        handle = path.open("rb")
        handle.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._range_remaining = end - start + 1
        return handle

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        try:
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        finally:
            self._range_remaining = None


def serve_dashboard(directory: Path, *, host: str, port: int) -> None:
    handler = partial(RangeRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    LOGGER.info("Germany map ready: http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping the local preview")
    finally:
        server.server_close()


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
