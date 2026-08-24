"""Validate and stream one HOSTRADA source month into Berlin-only outputs."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterator, Mapping

import numpy as np
from netCDF4 import Dataset
from psycopg import Connection

from src.database.connection import database_connection
from src.database.hostrada_state import ensure_hostrada_grid
from src.database.load import copy_rows
from src.database.spatial_state import current_geography_version
from src.hostrada_contract import (
    HOSTRADA_FIELD_CONTRACTS,
    HOSTRADA_REQUIRED_VARIABLES,
    HostradaMonthKey,
    validate_hostrada_month,
)
from src.hostrada_paths import HostradaPaths, hostrada_source_url


@dataclass(frozen=True)
class HostradaSourceRegistration:
    source_month: str
    source_grid_id: str
    source_file_count: int
    source_size_bytes: int
    source_hour_count: int
    duration_seconds: float


@dataclass(frozen=True)
class HostradaMonthlyLoad:
    source_month: str
    source_grid_id: str
    geography_version: str
    expected_hour_count: int
    retained_cell_count: int
    expected_plr_count: int
    source_cell_hour_count: int
    plr_hour_count: int
    berlin_hour_count: int
    staging_duration_seconds: float
    transformation_duration_seconds: float
    total_duration_seconds: float
    stage_table_bytes: int
    source_manifest_bytes: int
    plr_table_bytes: int
    berlin_table_bytes: int


@dataclass(frozen=True)
class BerlinCellWindow:
    y_indices: np.ndarray
    x_indices: np.ndarray
    y_start: int
    y_stop: int
    x_start: int
    x_stop: int

    @property
    def cell_count(self) -> int:
        return len(self.y_indices)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _open_sources(
    month: HostradaMonthKey,
    paths: HostradaPaths,
) -> Iterator[dict[str, Dataset]]:
    with ExitStack() as stack:
        datasets = {}
        for field in HOSTRADA_FIELD_CONTRACTS:
            source_path = paths.source_file(month, field.variable_name)
            if not source_path.is_file():
                raise FileNotFoundError(
                    f"HOSTRADA {field.variable_name} source does not exist: "
                    f"{source_path}"
                )
            datasets[field.variable_name] = stack.enter_context(
                Dataset(str(source_path))
            )
        yield datasets


def register_hostrada_month_sources(
    month: HostradaMonthKey,
    paths: HostradaPaths | None = None,
) -> HostradaSourceRegistration:
    started = perf_counter()
    resolved_paths = paths or HostradaPaths()

    with _open_sources(month, resolved_paths) as datasets:
        validated = validate_hostrada_month(datasets, month)
        source_files = []

        for field in HOSTRADA_FIELD_CONTRACTS:
            source_path = resolved_paths.source_file(month, field.variable_name)
            source_files.append(
                (
                    field,
                    source_path,
                    _sha256_file(source_path),
                    source_path.stat().st_size,
                )
            )

    with database_connection(
        application_name="capstone_hostrada_source_manifest"
    ) as connection:
        source_grid_id = ensure_hostrada_grid(connection)
        if source_grid_id != validated.source_grid_id:
            raise RuntimeError("Validated HOSTRADA source grid changed")

        connection.execute(
            """
            DELETE FROM raw.hostrada_month_source
            WHERE source_month_utc = %s::DATE
            """,
            (month.start_utc.date(),),
        )

        for field, source_path, checksum, source_size in source_files:
            connection.execute(
                """
                INSERT INTO raw.hostrada_month_source (
                    source_month_utc,
                    variable_name,
                    source_grid_id,
                    source_url,
                    source_path,
                    source_sha256,
                    source_size_bytes,
                    source_unit,
                    first_valid_time_utc,
                    last_valid_time_utc,
                    source_hour_count
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    month.start_utc.date(),
                    field.variable_name,
                    source_grid_id,
                    hostrada_source_url(month, field.variable_name),
                    str(source_path),
                    checksum,
                    source_size,
                    field.units,
                    validated.first_utc,
                    validated.last_utc,
                    validated.hour_count,
                ),
            )

    return HostradaSourceRegistration(
        source_month=month.partition_key,
        source_grid_id=validated.source_grid_id,
        source_file_count=len(source_files),
        source_size_bytes=sum(source_file[3] for source_file in source_files),
        source_hour_count=validated.hour_count,
        duration_seconds=perf_counter() - started,
    )


def _berlin_cell_window(
    connection: Connection,
    geography_version: str,
    source_grid_id: str,
) -> BerlinCellWindow:
    rows = connection.execute(
        """
        SELECT y_index, x_index
        FROM normalized.hostrada_cell
        WHERE geography_version = %s
          AND source_grid_id = %s
        ORDER BY y_index, x_index
        """,
        (geography_version, source_grid_id),
    ).fetchall()

    if not rows:
        raise RuntimeError(
            "No Berlin HOSTRADA cells exist; materialize hostrada_spatial first"
        )

    y_indices = np.asarray([row[0] for row in rows], dtype=np.int64)
    x_indices = np.asarray([row[1] for row in rows], dtype=np.int64)
    return BerlinCellWindow(
        y_indices=y_indices,
        x_indices=x_indices,
        y_start=int(y_indices.min()),
        y_stop=int(y_indices.max()) + 1,
        x_start=int(x_indices.min()),
        x_stop=int(x_indices.max()) + 1,
    )


def _hour_values(
    dataset: Any,
    variable_name: str,
    hour_index: int,
    window: BerlinCellWindow,
) -> np.ndarray:
    source_window = np.ma.asarray(
        dataset.variables[variable_name][
            hour_index,
            window.y_start:window.y_stop,
            window.x_start:window.x_stop,
        ],
        dtype=np.float64,
    )
    physical_values = np.asarray(
        np.ma.filled(source_window, np.nan),
        dtype=np.float64,
    )

    # Paired NumPy offsets select actual Berlin cells. Applying these index
    # arrays directly to a netCDF variable would instead form an outer product.
    return physical_values[
        window.y_indices - window.y_start,
        window.x_indices - window.x_start,
    ]


def _stage_rows(
    datasets: Mapping[str, Any],
    month: HostradaMonthKey,
    window: BerlinCellWindow,
    progress: Callable[[int, int], None] | None = None,
) -> Iterator[tuple[object, ...]]:
    for hour_index in range(month.hour_count):
        values = {
            variable_name: _hour_values(
                datasets[variable_name],
                variable_name,
                hour_index,
                window,
            )
            for variable_name in HOSTRADA_REQUIRED_VARIABLES
        }

        for variable_name, observed in values.items():
            missing_count = int((~np.isfinite(observed)).sum())
            if missing_count:
                raise ValueError(
                    f"HOSTRADA {variable_name} has {missing_count} missing "
                    f"Berlin values at hour {hour_index}"
                )

        humidity = values["hurs"]
        if np.any((humidity < 0.0) | (humidity > 100.0)):
            raise ValueError(
                f"HOSTRADA humidity is outside 0-100% at hour {hour_index}"
            )

        wind = values["sfcWind"]
        if np.any(wind < 0.0):
            raise ValueError(
                f"HOSTRADA wind speed is negative at hour {hour_index}"
            )

        valid_time_utc = month.start_utc + timedelta(hours=hour_index)
        for cell_index in range(window.cell_count):
            yield (
                valid_time_utc,
                int(window.y_indices[cell_index]),
                int(window.x_indices[cell_index]),
                float(values["tas"][cell_index]),
                float(humidity[cell_index]),
                float(wind[cell_index]),
            )

        if progress is not None and (
            (hour_index + 1) % 120 == 0
            or hour_index + 1 == month.hour_count
        ):
            progress(hour_index + 1, month.hour_count)


def load_hostrada_month(
    month: HostradaMonthKey,
    paths: HostradaPaths | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> HostradaMonthlyLoad:
    started = perf_counter()
    resolved_paths = paths or HostradaPaths()

    with _open_sources(month, resolved_paths) as datasets:
        validated = validate_hostrada_month(datasets, month)

        with database_connection(
            application_name="capstone_hostrada_monthly_weather"
        ) as connection:
            geography_version = current_geography_version(connection)
            source_grid_id = ensure_hostrada_grid(connection)
            if source_grid_id != validated.source_grid_id:
                raise RuntimeError("Validated HOSTRADA source grid changed")

            manifest = connection.execute(
                """
                SELECT variable_name, source_path, source_size_bytes
                FROM raw.hostrada_month_source
                WHERE source_month_utc = %s::DATE
                  AND source_grid_id = %s
                """,
                (month.start_utc.date(), source_grid_id),
            ).fetchall()

            observed_manifest = {str(row[0]): row for row in manifest}
            if set(observed_manifest) != set(HOSTRADA_REQUIRED_VARIABLES):
                raise RuntimeError(
                    "Register all three validated HOSTRADA sources first"
                )

            for variable_name in HOSTRADA_REQUIRED_VARIABLES:
                source_path = resolved_paths.source_file(month, variable_name)
                observed = observed_manifest[variable_name]
                if (
                    Path(str(observed[1])) != source_path
                    or int(observed[2]) != source_path.stat().st_size
                ):
                    raise RuntimeError(
                        f"HOSTRADA {variable_name} differs from its raw manifest"
                    )

            bridge_quality = connection.execute(
                """
                SELECT bridge.passed
                FROM normalized.check_hostrada_plr_area_bridge_quality(
                    %s::TEXT,
                    %s::TEXT,
                    (
                        SELECT COUNT(*)::INTEGER
                        FROM normalized.plr
                        WHERE geography_version = %s
                    )
                ) AS bridge
                """,
                (geography_version, source_grid_id, geography_version),
            ).fetchone()

            if bridge_quality is None or not bool(bridge_quality[0]):
                raise RuntimeError("HOSTRADA spatial bridge failed validation")

            window = _berlin_cell_window(
                connection,
                geography_version,
                source_grid_id,
            )

            connection.execute(
                """
                CREATE TEMPORARY TABLE hostrada_cell_hour_stage (
                    valid_time_utc TIMESTAMPTZ NOT NULL,
                    y_index INTEGER NOT NULL,
                    x_index INTEGER NOT NULL,
                    temperature_c DOUBLE PRECISION NOT NULL,
                    relative_humidity_percent DOUBLE PRECISION NOT NULL,
                    wind_speed_10m_ms DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (valid_time_utc, y_index, x_index),
                    CHECK (
                        relative_humidity_percent >= 0.0
                        AND relative_humidity_percent <= 100.0
                    ),
                    CHECK (wind_speed_10m_ms >= 0.0)
                ) ON COMMIT DROP
                """
            )

            staged = copy_rows(
                connection,
                schema="pg_temp",
                table="hostrada_cell_hour_stage",
                columns=(
                    "valid_time_utc",
                    "y_index",
                    "x_index",
                    "temperature_c",
                    "relative_humidity_percent",
                    "wind_speed_10m_ms",
                ),
                rows=_stage_rows(datasets, month, window, progress),
            )

            transformation_started = perf_counter()
            summary = connection.execute(
                """
                SELECT *
                FROM analytical.refresh_hostrada_month(
                    %s::DATE,
                    %s::TEXT,
                    %s::TEXT
                )
                """,
                (month.start_utc.date(), geography_version, source_grid_id),
            ).fetchone()
            transformation_duration = perf_counter() - transformation_started

            if summary is None:
                raise RuntimeError("HOSTRADA monthly SQL returned no summary")

            relation_sizes = connection.execute(
                """
                SELECT
                    pg_total_relation_size(
                        'pg_temp.hostrada_cell_hour_stage'::REGCLASS
                    ),
                    pg_total_relation_size(
                        'raw.hostrada_month_source'::REGCLASS
                    ),
                    pg_total_relation_size(
                        'analytical.hostrada_plr_hourly'::REGCLASS
                    ),
                    pg_total_relation_size(
                        'analytical.hostrada_berlin_hourly'::REGCLASS
                    )
                """
            ).fetchone()

            if relation_sizes is None:
                raise RuntimeError("HOSTRADA relation-size query returned no row")

    return HostradaMonthlyLoad(
        source_month=month.partition_key,
        source_grid_id=source_grid_id,
        geography_version=geography_version,
        expected_hour_count=int(summary[3]),
        retained_cell_count=int(summary[4]),
        expected_plr_count=int(summary[5]),
        source_cell_hour_count=int(summary[0]),
        plr_hour_count=int(summary[1]),
        berlin_hour_count=int(summary[2]),
        staging_duration_seconds=staged.duration_seconds,
        transformation_duration_seconds=transformation_duration,
        total_duration_seconds=perf_counter() - started,
        stage_table_bytes=int(relation_sizes[0]),
        source_manifest_bytes=int(relation_sizes[1]),
        plr_table_bytes=int(relation_sizes[2]),
        berlin_table_bytes=int(relation_sizes[3]),
    )
