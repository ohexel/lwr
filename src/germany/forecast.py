"""Germany forecast aggregation for one (run_time, lead_time) partition.

Two implementations of the same cell -> Gemeinde aggregation are run on the
same decoded fields (open decision 2, measured on purpose):

* SQL: stage the four raw indicators of all Germany cells, then PostgreSQL
  converts units, computes shade apparent temperature with Berlin's function
  normalized.calculate_apparent_temperature_shade_c, stores cell values and
  area-weights them to Gemeinden. Its result is the canonical stored one.
* Python: the same formula in numpy and a sparse weighted sum (np.bincount).
  Its Gemeinde values are written to a temporary table (to include the write
  cost) and compared with the SQL result.

Stage timings land in normalized.de_aggregation_benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import numpy as np
from psycopg import Connection

from src.database.connection import database_connection
from src.database.load import copy_rows
from src.forecast_key import ForecastKey, ProjectPaths
from src.germany.contract import GEOGRAPHY_VERSION
from src.germany.normalize import apply_normalized_schema
from src.icon_grid_contract import ICON_D2_GRID_CONTRACT
from src.ingestion.icon_d2_ruc_field import _prepare_field


FORECAST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "germany" / "forecast_schema.sql"
)
SOURCE_GRID_ID = ICON_D2_GRID_CONTRACT.source_grid_id
INDICATOR_COLUMNS = {
    "T_2M": "temperature_k",
    "RELHUM_2M": "relative_humidity_percent",
    "U_10M": "wind_u_10m_ms",
    "V_10M": "wind_v_10m_ms",
}


def apply_forecast_schema(connection: Connection) -> None:
    apply_normalized_schema(connection)
    connection.execute(FORECAST_SCHEMA_PATH.read_text(encoding="utf-8"))


def apparent_temperature_shade_c(
    temperature_c: np.ndarray,
    relative_humidity_percent: np.ndarray,
    wind_speed_10m_ms: np.ndarray,
) -> np.ndarray:
    """numpy twin of normalized.calculate_apparent_temperature_shade_c."""
    vapour_pressure_hpa = (
        (relative_humidity_percent / 100.0)
        * 6.105
        * np.exp(17.27 * temperature_c / (237.7 + temperature_c))
    )
    return temperature_c + 0.33 * vapour_pressure_hpa - 0.70 * wind_speed_10m_ms - 4.00


@dataclass(frozen=True)
class GermanyBridge:
    """Bridge as dense arrays for numpy: one entry per (cell, Gemeinde)."""

    cell_indices: np.ndarray      # Germany cells, ascending
    ars: list[str]                # Gemeinde keys, ascending
    row_gemeinde: np.ndarray      # bridge row -> position in ars
    row_cell_position: np.ndarray  # bridge row -> position in cell_indices
    row_fraction: np.ndarray      # fraction_of_gemeinde

    @property
    def gemeinde_weight_sums(self) -> np.ndarray:
        return np.bincount(
            self.row_gemeinde, weights=self.row_fraction, minlength=len(self.ars)
        )

    def aggregate(self, cell_values: np.ndarray) -> np.ndarray:
        weighted = np.bincount(
            self.row_gemeinde,
            weights=self.row_fraction * cell_values[self.row_cell_position],
            minlength=len(self.ars),
        )
        return weighted / self.gemeinde_weight_sums


def load_bridge(
    connection: Connection,
    geography_version: str = GEOGRAPHY_VERSION,
) -> GermanyBridge:
    cells = np.array(
        [
            row[0]
            for row in connection.execute(
                """
                SELECT cell_index FROM normalized.de_icon_cell
                WHERE geography_version = %s AND source_grid_id = %s
                ORDER BY cell_index
                """,
                (geography_version, SOURCE_GRID_ID),
            )
        ],
        dtype=np.int64,
    )
    rows = connection.execute(
        """
        SELECT ars, cell_index, fraction_of_gemeinde
        FROM normalized.de_icon_cell_gemeinde_bridge
        WHERE geography_version = %s AND source_grid_id = %s
        ORDER BY ars, cell_index
        """,
        (geography_version, SOURCE_GRID_ID),
    ).fetchall()
    if cells.size == 0 or not rows:
        raise RuntimeError(
            "Germany grid subset/bridge is empty; materialize "
            "normalized/de_icon_gemeinde_bridge first"
        )
    ars = sorted({row[0] for row in rows})
    ars_position = {key: position for position, key in enumerate(ars)}
    return GermanyBridge(
        cell_indices=cells,
        ars=ars,
        row_gemeinde=np.array([ars_position[r[0]] for r in rows], dtype=np.int64),
        row_cell_position=np.searchsorted(
            cells, np.array([r[1] for r in rows], dtype=np.int64)
        ),
        row_fraction=np.array([r[2] for r in rows], dtype=np.float64),
    )


@dataclass
class PartitionResult:
    run_time_utc: str
    lead_time: str
    cell_row_count: int = 0
    gemeinde_row_count: int = 0
    python_max_abs_difference_c: float | None = None
    timings: dict[str, float] = field(default_factory=dict)


def _decode_fields(
    forecast: ForecastKey,
    paths: ProjectPaths,
    cell_indices: np.ndarray,
) -> dict[str, np.ndarray]:
    values: dict[str, np.ndarray] = {}
    for indicator in INDICATOR_COLUMNS:
        prepared = _prepare_field(paths=paths, forecast=forecast, indicator=indicator)
        values[indicator] = prepared.values[cell_indices]
        del prepared
    return values


def _record(
    connection: Connection,
    forecast: ForecastKey,
    method: str,
    stage: str,
    seconds: float,
    row_count: int | None = None,
    max_abs_difference_c: float | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO normalized.de_aggregation_benchmark
            (run_time_utc, lead_time, method, stage, seconds, row_count,
             max_abs_difference_c)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            forecast.run_time,
            forecast.lead_time_label,
            method,
            stage,
            seconds,
            row_count,
            max_abs_difference_c,
        ),
    )


def _none_if_nan(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def process_partition(
    forecast: ForecastKey,
    *,
    paths: ProjectPaths | None = None,
    run_python: bool = True,
    geography_version: str = GEOGRAPHY_VERSION,
) -> PartitionResult:
    project_paths = paths if paths is not None else ProjectPaths()
    result = PartitionResult(
        run_time_utc=forecast.run_time.isoformat(),
        lead_time=forecast.lead_time_label,
    )
    timings = result.timings

    with database_connection(application_name="lwr_germany_forecast") as connection:
        apply_forecast_schema(connection)

        started = perf_counter()
        bridge = load_bridge(connection, geography_version)
        timings["shared_load_bridge"] = perf_counter() - started

        started = perf_counter()
        fields = _decode_fields(forecast, project_paths, bridge.cell_indices)
        timings["shared_decode_grib"] = perf_counter() - started

        # ---- SQL implementation (canonical) -------------------------------
        started = perf_counter()
        connection.execute(
            """
            CREATE TEMP TABLE de_cell_stage (
                cell_index integer PRIMARY KEY,
                temperature_k double precision,
                relative_humidity_percent double precision,
                wind_u_10m_ms double precision,
                wind_v_10m_ms double precision
            ) ON COMMIT DROP
            """
        )
        copy_rows(
            connection,
            schema="pg_temp",
            table="de_cell_stage",
            columns=("cell_index", *INDICATOR_COLUMNS.values()),
            rows=(
                (
                    int(cell),
                    *(_none_if_nan(fields[name][position]) for name in INDICATOR_COLUMNS),
                )
                for position, cell in enumerate(bridge.cell_indices)
            ),
        )
        timings["sql_stage_copy"] = perf_counter() - started

        started = perf_counter()
        counts = connection.execute(
            "SELECT * FROM normalized.refresh_de_forecast_partition(%s, %s, %s, %s, %s)",
            (
                forecast.run_time,
                forecast.lead_time_label,
                forecast.valid_time,
                geography_version,
                SOURCE_GRID_ID,
            ),
        ).fetchone()
        timings["sql_compute_and_store"] = perf_counter() - started
        result.cell_row_count, result.gemeinde_row_count = int(counts[0]), int(counts[1])

        for stage in ("sql_stage_copy", "sql_compute_and_store"):
            _record(connection, forecast, "sql", stage.removeprefix("sql_"),
                    timings[stage], result.gemeinde_row_count)
        for stage in ("shared_load_bridge", "shared_decode_grib"):
            _record(connection, forecast, "shared", stage.removeprefix("shared_"),
                    timings[stage], len(bridge.cell_indices))

        # ---- Python implementation (benchmark) ----------------------------
        if run_python:
            started = perf_counter()
            temperature_c = fields["T_2M"] - 273.15
            wind = np.sqrt(fields["U_10M"] ** 2 + fields["V_10M"] ** 2)
            apparent_c = apparent_temperature_shade_c(
                temperature_c, fields["RELHUM_2M"], wind
            )
            gemeinde_t = bridge.aggregate(temperature_c)
            gemeinde_a = bridge.aggregate(apparent_c)
            timings["python_compute"] = perf_counter() - started

            started = perf_counter()
            connection.execute(
                """
                CREATE TEMP TABLE de_python_gemeinde (
                    ars text PRIMARY KEY,
                    temperature_c double precision,
                    apparent_temperature_shade_c double precision
                ) ON COMMIT DROP
                """
            )
            copy_rows(
                connection,
                schema="pg_temp",
                table="de_python_gemeinde",
                columns=("ars", "temperature_c", "apparent_temperature_shade_c"),
                rows=(
                    (key, _none_if_nan(t), _none_if_nan(a))
                    for key, t, a in zip(bridge.ars, gemeinde_t, gemeinde_a)
                ),
            )
            timings["python_store_gemeinde"] = perf_counter() - started

            started = perf_counter()
            connection.execute(
                """
                CREATE TEMP TABLE de_python_cell (
                    cell_index integer PRIMARY KEY,
                    temperature_c double precision,
                    apparent_temperature_shade_c double precision
                ) ON COMMIT DROP
                """
            )
            copy_rows(
                connection,
                schema="pg_temp",
                table="de_python_cell",
                columns=("cell_index", "temperature_c", "apparent_temperature_shade_c"),
                rows=(
                    (int(cell), _none_if_nan(t), _none_if_nan(a))
                    for cell, t, a in zip(bridge.cell_indices, temperature_c, apparent_c)
                ),
            )
            timings["python_store_cells"] = perf_counter() - started

            difference = connection.execute(
                """
                SELECT MAX(GREATEST(
                    ABS(p.temperature_c - s.temperature_c),
                    ABS(p.apparent_temperature_shade_c - s.apparent_temperature_shade_c)
                ))
                FROM pg_temp.de_python_gemeinde AS p
                JOIN normalized.de_gemeinde_forecast AS s
                  ON s.ars = p.ars
                 AND s.run_time_utc = %s AND s.lead_time = %s
                 AND s.geography_version = %s
                """,
                (forecast.run_time, forecast.lead_time_label, geography_version),
            ).fetchone()[0]
            result.python_max_abs_difference_c = (
                None if difference is None else float(difference)
            )
            for stage in ("python_compute", "python_store_gemeinde", "python_store_cells"):
                _record(
                    connection, forecast, "python", stage.removeprefix("python_"),
                    timings[stage], len(bridge.ars),
                    result.python_max_abs_difference_c
                    if stage == "python_compute" else None,
                )

    return result
