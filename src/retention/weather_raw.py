import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.forecast_key import ProjectPaths, RUN_LABEL_FORMAT


WEATHER_RAW_RETENTION_DAYS_ENV = 'WEATHER_RAW_RETENTION_DAYS'
WEATHER_RAW_PINNED_PARTITIONS_ENV = 'WEATHER_RAW_PINNED_PARTITIONS'
DEFAULT_WEATHER_RAW_RETENTION_DAYS = 7


@dataclass(frozen=True)
class RetentionResult:
    retention_days: int
    cutoff_utc: datetime
    candidate_file_count: int
    deleted_file_count: int
    deleted_sidecar_file_count: int
    retained_recent_file_count: int
    retained_pinned_file_count: int
    bytes_deleted: int

def _sidecar_path(
        grib_path: Path
        ) -> Path:
    return ( grib_path.parent / "download_metadata.json" )

def _prune_empty_directories(
        *,
        start: Path,
        root: Path
        ) -> None:
    parent = start
    while parent != root and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break

        parent = parent.parent


def retention_days_from_env() -> int:
    value = int(
        os.getenv(
            WEATHER_RAW_RETENTION_DAYS_ENV,
            str(DEFAULT_WEATHER_RAW_RETENTION_DAYS),
        )
    )
    if value < 1:
        raise ValueError(
            f'{WEATHER_RAW_RETENTION_DAYS_ENV} must be at least 1'
        )
    return value


def pinned_partitions_from_env() -> frozenset[str]:
    raw_value = os.getenv(
        WEATHER_RAW_PINNED_PARTITIONS_ENV,
        '',
    )
    return frozenset(
        item.strip()
        for item in raw_value.split(',')
        if item.strip()
    )


def _partition_label(path: Path) -> str:
    # .../<indicator>/<run>/<lead>/<indicator>.grib2
    return f'{path.parents[1].name}/{path.parent.name}'


def _run_time(path: Path) -> datetime:
    run_label = path.parents[1].name
    return datetime.strptime(
        run_label,
        RUN_LABEL_FORMAT,
    ).replace(tzinfo=timezone.utc)


def prune_raw_weather_files(
    *,
    paths: ProjectPaths | None = None,
    retention_days: int | None = None,
    pinned_partitions: frozenset[str] | None = None,
    now_utc: datetime | None = None,
    dry_run: bool = True,
) -> RetentionResult:
    project_paths = paths if paths is not None else ProjectPaths()
    days = (
        retention_days
        if retention_days is not None
        else retention_days_from_env()
    )
    if days < 1:
        raise ValueError('retention_days must be at least 1')

    pins = (
        pinned_partitions
        if pinned_partitions is not None
        else pinned_partitions_from_env()
    )
    now = (
        now_utc
        if now_utc is not None
        else datetime.now(timezone.utc)
    )
    cutoff = now - timedelta(days=days)

    root = project_paths.data_root / 'raw' / 'icon_d2_ruc'
    candidates = sorted(root.glob('*/*/*/*.grib2'))

    deleted = 0
    deleted_sidecars = 0
    recent = 0
    pinned = 0
    bytes_deleted = 0

    for path in candidates:
        partition_label = _partition_label(path)
        if partition_label in pins:
            pinned += 1
            continue
        if _run_time(path) >= cutoff:
            recent += 1
            continue

        grib_size = path.stat().st_size

        sidecar_path = _sidecar_path(path)
        sidecar_exists = sidecar_path.is_file()
        sidecar_size = (
                sidecar_path.stat().st_size
                if sidecar_exists
                else 0
                )

        if not dry_run:
            path.unlink()

            if sidecar_exists:
                sidecar_path.unlink()

            _prune_empty_directories(
                    start = path.parent,
                    root = root
                    )

        deleted += 1

        if sidecar_exists:
            deleted_sidecars += 1

        bytes_deleted += (
                grib_size
                + sidecar_size
                )

    return RetentionResult(
        retention_days=days,
        cutoff_utc=cutoff,
        candidate_file_count=len(candidates),
        deleted_file_count=deleted,
        deleted_sidecar_file_count=(deleted_sidecars),
        retained_recent_file_count=recent,
        retained_pinned_file_count=pinned,
        bytes_deleted=bytes_deleted,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Prune retained local ICON-D2 RUC GRIB files.'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Actually delete files. Default is dry-run.',
    )
    args = parser.parse_args()

    result = prune_raw_weather_files(
        dry_run=not args.apply
    )
    print(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
