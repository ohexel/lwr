from dataclasses import asdict

import dagster as dg

from src.database.connection import database_connection
from src.dagster_pipeline.assets.germany_normalized import (
    NORMALIZED_DE_HIERARCHY_KEY,
    NORMALIZED_DE_POPULATION_KEY,
)
from src.dagster_pipeline.assets.germany_raw import GERMANY_GROUP
from src.dagster_pipeline.assets.icon_d2_ruc import RAW_ICON_D2_RUC_ASSETS
from src.dagster_pipeline.assets.database_spatial import RAW_ICON_GRID_KEY
from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.germany.contract import GEOGRAPHY_VERSION
from src.germany.forecast import (
    SOURCE_GRID_ID,
    apply_forecast_schema,
    process_partition,
)


NORMALIZED_DE_ICON_BRIDGE_KEY = dg.AssetKey(["normalized", "de_icon_gemeinde_bridge"])
NORMALIZED_DE_GEMEINDE_FORECAST_KEY = dg.AssetKey(["normalized", "de_gemeinde_forecast"])
ANALYTICAL_DE_CURRENT_FORECAST_KEY = dg.AssetKey(["analytical", "de_current_forecast_25h"])

# Open decision 3 (Ole, 2026-11-09): keep the newest complete run and the one
# before it. Database rows only; GRIB files follow the existing file rule.
KEEP_COMPLETE_RUNS = 2


@dg.asset(
    key=NORMALIZED_DE_ICON_BRIDGE_KEY,
    deps=[NORMALIZED_DE_HIERARCHY_KEY, RAW_ICON_GRID_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "Germany grid subset (ICON cells intersecting a Gemeinde, transformed "
        "from the Berlin cell polygons to EPSG:3035) and the cell-Gemeinde area "
        "bridge. Berlin's mask and bridges are read, never written."
    ),
)
def normalized_de_icon_gemeinde_bridge(context: dg.AssetExecutionContext) -> None:
    with database_connection(application_name="lwr_germany_bridge") as connection:
        apply_forecast_schema(connection)
        cells, rows, gemeinden = connection.execute(
            "SELECT * FROM normalized.refresh_de_icon_bridge(%s, %s)",
            (GEOGRAPHY_VERSION, SOURCE_GRID_ID),
        ).fetchone()
    context.add_output_metadata(
        {
            "geography_version": GEOGRAPHY_VERSION,
            "source_grid_id": SOURCE_GRID_ID,
            "cell_count": int(cells),
            "bridge_row_count": int(rows),
            "gemeinde_count": int(gemeinden),
        }
    )


@dg.asset(
    key=NORMALIZED_DE_GEMEINDE_FORECAST_KEY,
    partitions_def=WEATHER_PARTITIONS,
    deps=[*(asset.key for asset in RAW_ICON_D2_RUC_ASSETS), NORMALIZED_DE_ICON_BRIDGE_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "Per-cell temperature and shade apparent temperature for all Germany "
        "cells and their area-weighted Gemeinde values for one forecast "
        "partition (SQL)."
    ),
)
def normalized_de_gemeinde_forecast(context: dg.AssetExecutionContext) -> None:
    forecast = forecast_key_from_partition(context.partition_key)
    result = process_partition(forecast)
    metadata = {key: value for key, value in asdict(result).items() if key != "timings"}
    metadata.update({f"seconds_{key}": round(value, 4) for key, value in result.timings.items()})
    context.add_output_metadata(metadata)


@dg.asset(
    key=ANALYTICAL_DE_CURRENT_FORECAST_KEY,
    deps=[NORMALIZED_DE_GEMEINDE_FORECAST_KEY, NORMALIZED_DE_POPULATION_KEY],
    group_name=GERMANY_GROUP,
    description=(
        "Publishes the current complete Germany forecast: the de_current_* "
        "views (Gemeinde, Kreis, Land, Bund; area- and population-weighted "
        "roll-ups; Gemeinde, Kreis, Land and Bund 24-hour summaries) and "
        "applies retention (newest complete run plus the previous one)."
    ),
)
def analytical_de_current_forecast_25h(context: dg.AssetExecutionContext) -> None:
    with database_connection(application_name="lwr_germany_publish") as connection:
        apply_forecast_schema(connection)
        current = connection.execute(
            "SELECT run_time_utc FROM analytical.de_current_run"
        ).fetchone()
        if current is None:
            raise RuntimeError("No complete Germany forecast run (25 lead times) exists")
        kept, deleted_cells, deleted_gemeinden = connection.execute(
            "SELECT * FROM normalized.prune_de_forecast(%s, %s)",
            (GEOGRAPHY_VERSION, KEEP_COMPLETE_RUNS),
        ).fetchone()
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM analytical.de_current_gemeinde_forecast_25h),
                (SELECT COUNT(*) FROM analytical.de_current_kreis_forecast_25h),
                (SELECT COUNT(*) FROM analytical.de_current_land_forecast_25h),
                (SELECT COUNT(*) FROM analytical.de_current_bund_forecast_25h),
                (SELECT COUNT(*) FROM analytical.de_current_gemeinde_summary),
                (SELECT COUNT(*) FROM analytical.de_current_kreis_summary),
                (SELECT COUNT(*) FROM analytical.de_current_land_summary),
                (SELECT COUNT(DISTINCT run_time_utc) FROM normalized.de_gemeinde_forecast),
                pg_total_relation_size('normalized.de_icon_cell_value'),
                pg_total_relation_size('normalized.de_gemeinde_forecast')
            """
        ).fetchone()
    context.add_output_metadata(
        {
            "current_run_time_utc": current[0].isoformat(),
            "retention_oldest_kept_run_utc": None if kept is None else kept.isoformat(),
            "retention_deleted_cell_rows": int(deleted_cells),
            "retention_deleted_gemeinde_rows": int(deleted_gemeinden),
            "gemeinde_rows": int(counts[0]),
            "kreis_rows": int(counts[1]),
            "land_rows": int(counts[2]),
            "bund_rows": int(counts[3]),
            "gemeinde_summary_rows": int(counts[4]),
            "kreis_summary_rows": int(counts[5]),
            "land_summary_rows": int(counts[6]),
            "retained_runs": int(counts[7]),
            "cell_value_bytes": int(counts[8]),
            "gemeinde_forecast_bytes": int(counts[9]),
        }
    )


GERMANY_FORECAST_ASSETS = [
    normalized_de_icon_gemeinde_bridge,
    normalized_de_gemeinde_forecast,
    analytical_de_current_forecast_25h,
]
