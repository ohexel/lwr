"""Apply one bounded retention window to operational forecast data."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, ContextManager

from src.database.connection import database_connection
from src.forecast_key import ProjectPaths, RUN_LABEL_FORMAT
from src.retention.forecast_policy import (
    forecast_retention_cutoff,
    forecast_retention_hours,
)


# Children precede parents so row counts remain explicit despite cascading
# foreign keys. Historical, reference, geography, and population tables never
# appear in this allowlist.
FORECAST_RETENTION_TABLES = (
    "analytical.plr_weather_population_rejected",
    "analytical.plr_weather_population",
    "analytical.plr_weather",
    "normalized.weather_partition_rejected",
    "normalized.icon_d2_ruc_weather",
    "raw.icon_d2_ruc_field",
    "raw.icon_d2_ruc_source",
)


@dataclass(frozen=True)
class RetentionResult:
    retention_hours: int
    cutoff_utc: datetime
    candidate_file_count: int
    deleted_file_count: int
    deleted_sidecar_file_count: int
    retained_recent_file_count: int
    bytes_deleted: int


@dataclass(frozen=True)
class DatabaseRetentionResult:
    retention_hours: int
    cutoff_utc: datetime
    affected_rows_by_table: dict[str, int]

    @property
    def total_affected_rows(self) -> int:
        return sum(self.affected_rows_by_table.values())


@dataclass(frozen=True)
class ForecastRetentionResult:
    retention_hours: int
    cutoff_utc: datetime
    database: DatabaseRetentionResult
    raw_files: RetentionResult


def _sidecar_path(grib_path: Path) -> Path:
    return grib_path.parent / "download_metadata.json"


def _prune_empty_directories(*, start: Path, root: Path) -> None:
    parent = start
    while parent != root and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _run_time(path: Path) -> datetime:
    run_label = path.parents[1].name
    return datetime.strptime(run_label, RUN_LABEL_FORMAT).replace(
        tzinfo=timezone.utc
    )


def prune_raw_weather_files(
    *,
    paths: ProjectPaths | None = None,
    retention_hours: int | None = None,
    now_utc: datetime | None = None,
    dry_run: bool = True,
) -> RetentionResult:
    """Remove expired forecast GRIB files and their acquisition sidecars."""
    project_paths = paths if paths is not None else ProjectPaths()
    hours = forecast_retention_hours(retention_hours)
    cutoff = forecast_retention_cutoff(
        now_utc=now_utc,
        retention_hours=hours,
    )

    root = project_paths.data_root / "raw" / "icon_d2_ruc"
    candidates = sorted(root.glob("*/*/*/*.grib2"))

    deleted = 0
    deleted_sidecars = 0
    recent = 0
    bytes_deleted = 0

    for path in candidates:
        if _run_time(path) >= cutoff:
            recent += 1
            continue

        grib_size = path.stat().st_size
        sidecar_path = _sidecar_path(path)
        sidecar_exists = sidecar_path.is_file()
        sidecar_size = sidecar_path.stat().st_size if sidecar_exists else 0

        if not dry_run:
            path.unlink()
            if sidecar_exists:
                sidecar_path.unlink()
            _prune_empty_directories(start=path.parent, root=root)

        deleted += 1
        if sidecar_exists:
            deleted_sidecars += 1
        bytes_deleted += grib_size + sidecar_size

    return RetentionResult(
        retention_hours=hours,
        cutoff_utc=cutoff,
        candidate_file_count=len(candidates),
        deleted_file_count=deleted,
        deleted_sidecar_file_count=deleted_sidecars,
        retained_recent_file_count=recent,
        bytes_deleted=bytes_deleted,
    )


def prune_forecast_database_rows(
    *,
    retention_hours: int | None = None,
    now_utc: datetime | None = None,
    dry_run: bool = True,
    connection_factory: Callable[..., ContextManager] | None = None,
) -> DatabaseRetentionResult:
    """Inspect or atomically delete expired rows from forecast-only tables."""
    hours = forecast_retention_hours(retention_hours)
    cutoff = forecast_retention_cutoff(
        now_utc=now_utc,
        retention_hours=hours,
    )
    connect = connection_factory if connection_factory is not None else database_connection
    affected_rows: dict[str, int] = {}

    with connect(application_name="capstone_forecast_retention") as connection:
        for table_name in FORECAST_RETENTION_TABLES:
            if dry_run:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM {table_name} "
                    "WHERE run_time_utc < %s",
                    (cutoff,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"Forecast retention count returned no row for {table_name}"
                    )
                affected_rows[table_name] = int(row[0])
            else:
                cursor = connection.execute(
                    f"DELETE FROM {table_name} WHERE run_time_utc < %s",
                    (cutoff,),
                )
                affected_rows[table_name] = int(cursor.rowcount)

    return DatabaseRetentionResult(
        retention_hours=hours,
        cutoff_utc=cutoff,
        affected_rows_by_table=affected_rows,
    )


def prune_forecast_data(
    *,
    paths: ProjectPaths | None = None,
    retention_hours: int | None = None,
    now_utc: datetime | None = None,
    dry_run: bool = True,
    connection_factory: Callable[..., ContextManager] | None = None,
) -> ForecastRetentionResult:
    """Apply the same UTC cutoff to PostgreSQL rows and raw forecast files."""
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    hours = forecast_retention_hours(retention_hours)
    database_result = prune_forecast_database_rows(
        retention_hours=hours,
        now_utc=now,
        dry_run=dry_run,
        connection_factory=connection_factory,
    )
    raw_result = prune_raw_weather_files(
        paths=paths,
        retention_hours=hours,
        now_utc=now,
        dry_run=dry_run,
    )

    return ForecastRetentionResult(
        retention_hours=hours,
        cutoff_utc=database_result.cutoff_utc,
        database=database_result,
        raw_files=raw_result,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or prune forecast PostgreSQL rows and raw GRIB files "
            "using the configured rolling retention window of at most 24 hours."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete expired forecast rows and files. Default is dry-run.",
    )
    args = parser.parse_args()

    result = prune_forecast_data(dry_run=not args.apply)
    print(json.dumps(asdict(result), default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
