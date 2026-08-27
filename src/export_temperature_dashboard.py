"""Export a self-contained static Berlin neighborhood temperature dashboard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import shutil

import psycopg

from src.database.connection import database_connection


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_SOURCE = PROJECT_ROOT / "web" / "temperature-dashboard"
PUBLIC_ASSETS = ("index.html", "styles.css", "app.js")
EXPECTED_LEAD_COUNT = 25
HISTORICAL_YEARS = tuple(range(1995, 2026))

SCHEMA_QUERY = """
SELECT
    to_regclass('normalized.plr') IS NOT NULL,
    to_regclass('analytical.current_plr_temperature_forecast_25h') IS NOT NULL,
    to_regclass('analytical.current_plr_temperature_summary_25h') IS NOT NULL,
    to_regclass('analytical.current_plr_temperature_history_25h') IS NOT NULL,
    (SELECT COUNT(*) FROM analytical.plr_display_name)
"""

SUMMARY_QUERY = """
SELECT
    summary.plr_id,
    summary.plr_name,
    summary.run_time_berlin,
    summary.max_forecast_temperature_c,
    summary.max_forecast_temperature_at_berlin,
    summary.max_temperature_difference_c,
    summary.max_temperature_difference_at_berlin,
    summary.max_apparent_temperature_difference_c,
    summary.max_apparent_temperature_difference_at_berlin,
    summary.sum_temperature_difference_c,
    summary.population_total,
    summary.population_65plus,
    summary.population_status,
    ST_AsGeoJSON(
        ST_SimplifyPreserveTopology(geography.geometry, %s),
        1
    )::jsonb AS geometry
FROM analytical.current_plr_temperature_summary_25h AS summary
JOIN analytical.plr_display_name AS display_name
  ON display_name.plr_id = summary.plr_id
 AND display_name.plr_name = summary.plr_name
JOIN normalized.plr AS geography
  ON geography.plr_id = display_name.plr_id
 AND geography.geography_version = display_name.geography_version
ORDER BY summary.plr_id
"""

FORECAST_QUERY = """
SELECT
    forecast.plr_id,
    jsonb_agg(
        to_char(forecast.valid_time_berlin, 'YYYY-MM-DD"T"HH24:MI')
        ORDER BY forecast.lead_hour
    ) AS valid_times_berlin,
    jsonb_agg(
        round(forecast.forecast_temperature_c::numeric, 2)
        ORDER BY forecast.lead_hour
    ) AS forecast_temperatures_c,
    jsonb_agg(
        round(forecast.forecast_apparent_temperature_c::numeric, 2)
        ORDER BY forecast.lead_hour
    ) AS forecast_apparent_temperatures_c,
    jsonb_agg(
        round(forecast.historical_temperature_median_c::numeric, 2)
        ORDER BY forecast.lead_hour
    ) AS historical_median_temperatures_c,
    jsonb_agg(
        round(forecast.historical_apparent_temperature_median_c::numeric, 2)
        ORDER BY forecast.lead_hour
    ) AS historical_median_apparent_temperatures_c,
    COUNT(*) AS lead_count
FROM analytical.current_plr_temperature_forecast_25h AS forecast
GROUP BY forecast.plr_id
ORDER BY forecast.plr_id
"""

HISTORY_QUERY = """
WITH historical_years AS (
    SELECT
        history.plr_id,
        history.historical_year,
        jsonb_agg(
            round(history.historical_temperature_c::numeric, 2)
            ORDER BY history.lead_hour
        ) AS temperatures_c,
        jsonb_agg(
            round(history.historical_apparent_temperature_c::numeric, 2)
            ORDER BY history.lead_hour
        ) AS apparent_temperatures_c,
        COUNT(*) AS lead_count
    FROM analytical.current_plr_temperature_history_25h AS history
    GROUP BY history.plr_id, history.historical_year
)
SELECT
    historical.plr_id,
    jsonb_agg(
        jsonb_build_object(
            'year', historical.historical_year,
            'temperatures_c', historical.temperatures_c,
            'apparent_temperatures_c', historical.apparent_temperatures_c
        )
        ORDER BY historical.historical_year
    ) AS historical_years,
    bool_and(historical.lead_count = 25) AS all_years_complete
FROM historical_years AS historical
GROUP BY historical.plr_id
ORDER BY historical.plr_id
"""


def round_temperature(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError("Dashboard temperatures must be finite numbers.")
    return round(number, 2)


def format_local_time(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise RuntimeError("Forecast timestamps must be PostgreSQL datetime values.")
    return value.replace(tzinfo=None).isoformat(timespec="minutes")


def write_json_atomic(path: Path, payload: object) -> None:
    """Publish one complete UTF-8 JSON file without exposing partial bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install_public_assets(destination: Path) -> None:
    """Support both editable in-project previews and standalone publish roots."""
    destination.mkdir(parents=True, exist_ok=True)
    if destination.resolve() == DASHBOARD_SOURCE.resolve():
        return

    for asset_name in PUBLIC_ASSETS:
        shutil.copy2(DASHBOARD_SOURCE / asset_name, destination / asset_name)


def summary_record(row: tuple[object, ...]) -> dict[str, object]:
    (
        plr_id,
        plr_name,
        run_time,
        maximum_temperature,
        maximum_temperature_at,
        maximum_difference,
        maximum_difference_at,
        maximum_apparent_difference,
        maximum_apparent_difference_at,
        summed_difference,
        population_total,
        population_65plus,
        population_status,
        geometry,
    ) = row

    if not isinstance(geometry, dict) or geometry.get("type") not in {
        "Polygon",
        "MultiPolygon",
    }:
        raise RuntimeError(f"PLR {plr_id} has no usable polygon geometry.")

    return {
        "id": str(plr_id),
        "name": str(plr_name),
        "run_time_berlin": format_local_time(run_time),
        "maximum_temperature_c": round_temperature(maximum_temperature),
        "maximum_temperature_at_berlin": format_local_time(maximum_temperature_at),
        "maximum_difference_c": round_temperature(maximum_difference),
        "maximum_difference_at_berlin": format_local_time(maximum_difference_at),
        "maximum_apparent_temperature_difference_c": round_temperature(
            maximum_apparent_difference
        ),
        "maximum_apparent_temperature_difference_at_berlin": format_local_time(
            maximum_apparent_difference_at
        ),
        "summed_difference_c": round_temperature(summed_difference),
        "population_total": (
            int(population_total) if population_total is not None else None
        ),
        "population_65plus": (
            int(population_65plus) if population_65plus is not None else None
        ),
        "population_status": str(population_status),
        "geometry": geometry,
    }


def validate_historical_lines(plr_id: str, lines: object) -> list[dict[str, object]]:
    if not isinstance(lines, list):
        raise RuntimeError(f"PLR {plr_id} has invalid historical plotting data.")

    actual_years = tuple(int(item["year"]) for item in lines)
    if actual_years != HISTORICAL_YEARS:
        raise RuntimeError(
            f"PLR {plr_id} does not contain all historical years 1995-2025."
        )

    for line in lines:
        temperatures = line.get("temperatures_c")
        apparent_temperatures = line.get("apparent_temperatures_c")
        if not isinstance(temperatures, list) or len(temperatures) != EXPECTED_LEAD_COUNT:
            raise RuntimeError(
                f"PLR {plr_id}, historical year {line['year']}, "
                "does not contain 25 temperature observations."
            )
        if (
            not isinstance(apparent_temperatures, list)
            or len(apparent_temperatures) != EXPECTED_LEAD_COUNT
        ):
            raise RuntimeError(
                f"PLR {plr_id}, historical year {line['year']}, "
                "does not contain 25 apparent-temperature observations."
            )
        line["temperatures_c"] = [round_temperature(value) for value in temperatures]
        line["apparent_temperatures_c"] = [
            round_temperature(value) for value in apparent_temperatures
        ]

    return lines


def export_temperature_dashboard(
    *,
    output_dir: Path = DASHBOARD_SOURCE,
    simplify_meters: float = 20.0,
) -> dict[str, object]:
    """Export the current complete horizon as independently hostable files."""
    if not math.isfinite(simplify_meters) or simplify_meters <= 0:
        raise ValueError("Geometry simplification must be a positive meter value.")

    with database_connection(
        application_name="capstone_static_temperature_dashboard"
    ) as connection:
        installed = connection.execute(SCHEMA_QUERY).fetchone()
        if installed is None or not all(installed[:3]):
            raise RuntimeError(
                "The 25-hour forecast views or PLR geometry are unavailable. "
                "Run: bash scripts/bootstrap_database.sh"
            )

        history_view_installed = bool(installed[3])
        expected_plr_count = int(installed[4])
        summaries = [
            summary_record(row)
            for row in connection.execute(
                SUMMARY_QUERY,
                (simplify_meters,),
            ).fetchall()
        ]

        if expected_plr_count < 1 or len(summaries) != expected_plr_count:
            raise RuntimeError(
                "A complete 25-hour forecast and exactly one geometry per "
                f"PLR are required: expected {expected_plr_count}, "
                f"observed {len(summaries)}."
            )

        run_times = {str(area["run_time_berlin"]) for area in summaries}
        if len(run_times) != 1:
            raise RuntimeError("Dashboard summaries must describe one forecast run.")

        forecast_rows = connection.execute(FORECAST_QUERY).fetchall()
        histories = (
            connection.execute(HISTORY_QUERY).fetchall()
            if history_view_installed
            else []
        )

    forecast_by_id = {}
    for (
        plr_id,
        valid_times,
        temperatures,
        apparent_temperatures,
        medians,
        apparent_medians,
        lead_count,
    ) in forecast_rows:
        if (
            int(lead_count) != EXPECTED_LEAD_COUNT
            or len(valid_times) != EXPECTED_LEAD_COUNT
            or len(temperatures) != EXPECTED_LEAD_COUNT
            or len(apparent_temperatures) != EXPECTED_LEAD_COUNT
            or len(medians) != EXPECTED_LEAD_COUNT
            or len(apparent_medians) != EXPECTED_LEAD_COUNT
        ):
            raise RuntimeError(f"PLR {plr_id} does not contain all 25 forecast hours.")
        forecast_by_id[str(plr_id)] = {
            "valid_times_berlin": valid_times,
            "forecast_temperatures_c": [
                round_temperature(value) for value in temperatures
            ],
            "forecast_apparent_temperatures_c": [
                round_temperature(value) for value in apparent_temperatures
            ],
            "historical_median_temperatures_c": [
                round_temperature(value) for value in medians
            ],
            "historical_median_apparent_temperatures_c": [
                round_temperature(value) for value in apparent_medians
            ],
        }

    history_by_id = {}
    for plr_id, lines, all_years_complete in histories:
        if not all_years_complete:
            raise RuntimeError(f"PLR {plr_id} has an incomplete historical year.")
        history_by_id[str(plr_id)] = validate_historical_lines(str(plr_id), lines)

    summary_ids = {str(area["id"]) for area in summaries}
    if set(forecast_by_id) != summary_ids:
        raise RuntimeError("Forecast plotting data does not match the PLR summaries.")
    if history_by_id and set(history_by_id) != summary_ids:
        raise RuntimeError("Historical plotting data does not cover every PLR.")

    destination = output_dir.resolve()
    install_public_assets(destination)
    data_dir = destination / "data"
    detail_dir = data_dir / "areas"

    for area in summaries:
        plr_id = str(area["id"])
        write_json_atomic(
            detail_dir / f"{plr_id}.json",
            {
                "plr_id": plr_id,
                "plr_name": area["name"],
                "run_time_berlin": area["run_time_berlin"],
                **forecast_by_id[plr_id],
                "historical_years": history_by_id.get(plr_id, []),
            },
        )

    run_time = next(iter(run_times))
    map_payload = {
        "format_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_time_berlin": run_time,
        "coordinate_system": "EPSG:25833",
        "lead_hour_count": EXPECTED_LEAD_COUNT,
        "historical_year_count": len(HISTORICAL_YEARS) if history_by_id else 0,
        "areas": summaries,
    }
    write_json_atomic(data_dir / "map.json", map_payload)

    return {
        "status": "ready",
        "output_directory": str(destination),
        "run_time_berlin": run_time,
        "plr_count": expected_plr_count,
        "lead_hour_count": EXPECTED_LEAD_COUNT,
        "historical_year_count": len(HISTORICAL_YEARS) if history_by_id else 0,
        "detail_file_count": len(summaries),
        "simplification_meters": simplify_meters,
    }


def serve_dashboard(directory: Path, *, host: str, port: int) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    LOGGER.info("Dashboard ready: http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping the local dashboard preview")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a lightweight, static Berlin neighborhood temperature "
            "map and optionally serve it locally. No frontend framework, "
            "map tiles, CDN, or production database connection is required."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DASHBOARD_SOURCE,
        help="Standalone output root; defaults to web/temperature-dashboard.",
    )
    parser.add_argument(
        "--simplify-meters",
        type=float,
        default=20.0,
        help="Topology-preserving PLR boundary simplification in meters.",
    )
    parser.add_argument("--serve", action="store_true", help="Start a local preview.")
    parser.add_argument("--host", default="127.0.0.1", help="Local preview address.")
    parser.add_argument("--port", type=int, default=8765, help="Local preview port.")
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )

    try:
        result = export_temperature_dashboard(
            output_dir=arguments.output_dir,
            simplify_meters=arguments.simplify_meters,
        )
    except (OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    if arguments.serve:
        serve_dashboard(
            Path(result["output_directory"]),
            host=arguments.host,
            port=arguments.port,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
