"""Resume a bounded HOSTRADA history backfill with one-month prefetch."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import perf_counter
from typing import Iterator

import dagster as dg

from src.database.connection import database_connection
from src.database.hostrada_state import query_hostrada_month_quality
from src.database.spatial_state import current_geography_version
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT, HostradaMonthKey
from src.hostrada_paths import HostradaPaths
from src.ingestion.hostrada_download import (
    DEFAULT_DOWNLOAD_ATTEMPTS,
    HostradaMonthDownload,
    download_hostrada_month,
)
from src.retention.hostrada_raw import delete_verified_hostrada_sources


LOGGER = logging.getLogger(__name__)
BYTES_PER_GIB = 1024 ** 3
DEFAULT_MINIMUM_FREE_GIB = 15.0


@dataclass
class HostradaBackfillSummary:
    requested_month_count: int
    completed_month_count: int = 0
    skipped_month_count: int = 0
    deleted_file_count: int = 0
    deleted_bytes: int = 0


def iter_hostrada_months(
    start: HostradaMonthKey,
    end: HostradaMonthKey,
) -> Iterator[HostradaMonthKey]:
    if start.start_utc > end.start_utc:
        raise ValueError("HOSTRADA backfill start must not be later than its end")

    current = start
    while current.start_utc <= end.start_utc:
        yield current
        if current == end:
            break
        next_start = current.end_utc
        current = HostradaMonthKey(next_start.year, next_start.month)


def ensure_free_space(
    paths: tuple[Path, ...],
    minimum_free_gib: float,
) -> None:
    required_bytes = minimum_free_gib * BYTES_PER_GIB
    for path in paths:
        available_bytes = shutil.disk_usage(path).free
        if available_bytes < required_bytes:
            raise RuntimeError(
                f"Insufficient free space at {path}: "
                f"{available_bytes / BYTES_PER_GIB:.2f} GiB available; "
                f"{minimum_free_gib:.2f} GiB required"
            )


@contextmanager
def backfill_process_lock(paths: HostradaPaths) -> Iterator[None]:
    root = paths.project_root / "data" / "raw" / "hostrada"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".hostrada-backfill.lock"

    with lock_path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"Another HOSTRADA backfill already holds {lock_path}"
            ) from error

        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def verify_backfill_prerequisites() -> None:
    with database_connection(
        application_name="capstone_hostrada_backfill_preflight"
    ) as connection:
        # Fail before any download if the retention migration was not applied.
        connection.execute(
            "SELECT source_deleted_at_utc FROM raw.hostrada_month_source LIMIT 0"
        )
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT bridge.passed
            FROM normalized.check_hostrada_plr_area_bridge_quality(
                %s::TEXT,
                %s::TEXT
            ) AS bridge
            """,
            (
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None or result[0] is not True:
        raise RuntimeError("The HOSTRADA spatial bridge failed its preflight check")


def execute_hostrada_partition(
    month: HostradaMonthKey,
    instance: dg.DagsterInstance,
) -> None:
    from src.dagster_pipeline.definitions import defs

    result = defs.get_job_def("hostrada_monthly").execute_in_process(
        partition_key=month.partition_key,
        instance=instance,
        raise_on_error=False,
        tags={"hostrada/backfill": "historical"},
    )
    if not result.success:
        raise RuntimeError(
            "Dagster HOSTRADA monthly job failed for partition "
            f"{month.partition_key}; source files were retained"
        )

    LOGGER.info(
        "hostrada_dagster_partition_succeeded month=%s run_id=%s",
        month.partition_key,
        result.run_id,
    )


def run_hostrada_backfill(
    *,
    start: HostradaMonthKey,
    end: HostradaMonthKey,
    instance: dg.DagsterInstance,
    paths: HostradaPaths | None = None,
    minimum_free_gib: float = DEFAULT_MINIMUM_FREE_GIB,
    storage_paths: tuple[Path, ...] = (),
    max_download_attempts: int = DEFAULT_DOWNLOAD_ATTEMPTS,
    prefetch: bool = True,
) -> HostradaBackfillSummary:
    if minimum_free_gib < 0:
        raise ValueError("Minimum free space must not be negative")
    if max_download_attempts < 1:
        raise ValueError("HOSTRADA download attempts must be at least one")

    resolved_paths = paths or HostradaPaths()
    months = tuple(iter_hostrada_months(start, end))
    summary = HostradaBackfillSummary(requested_month_count=len(months))
    watched_paths = tuple(
        dict.fromkeys((resolved_paths.project_root, *storage_paths))
    )
    stopped = Event()
    started = perf_counter()

    def check_free_space() -> None:
        ensure_free_space(watched_paths, minimum_free_gib)

    def pending_months() -> Iterator[HostradaMonthKey]:
        for month in months:
            quality = query_hostrada_month_quality(month)
            if quality.passed:
                cleanup = delete_verified_hostrada_sources(month, resolved_paths)
                summary.skipped_month_count += 1
                summary.deleted_file_count += cleanup.deleted_file_count
                summary.deleted_bytes += cleanup.bytes_deleted
                LOGGER.info(
                    "hostrada_month_skipped month=%s reason=verified_outputs "
                    "skipped=%s total=%s",
                    month.partition_key,
                    summary.skipped_month_count,
                    summary.requested_month_count,
                )
                continue
            yield month

    def submit_download(
        executor: ThreadPoolExecutor,
        month: HostradaMonthKey,
    ) -> Future[HostradaMonthDownload]:
        LOGGER.info("hostrada_download_submitted month=%s", month.partition_key)
        return executor.submit(
            download_hostrada_month,
            month,
            resolved_paths,
            max_attempts=max_download_attempts,
            stop_event=stopped,
            check_free_space=check_free_space,
        )

    with backfill_process_lock(resolved_paths):
        check_free_space()
        verify_backfill_prerequisites()
        remaining = iter(pending_months())
        current = next(remaining, None)

        if current is None:
            LOGGER.info(
                "hostrada_backfill_already_complete months=%s",
                summary.requested_month_count,
            )
            return summary

        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="hostrada-prefetch",
        ) as executor:
            current_download = submit_download(executor, current)
            next_download = None

            try:
                while current is not None:
                    download_result = current_download.result()
                    following = next(remaining, None)

                    if following is not None and prefetch:
                        next_download = submit_download(executor, following)
                        LOGGER.info(
                            "hostrada_prefetch_overlapping processing_month=%s "
                            "download_month=%s",
                            current.partition_key,
                            following.partition_key,
                        )

                    check_free_space()
                    LOGGER.info(
                        "hostrada_month_processing month=%s source_bytes=%s "
                        "source_download_seconds=%.2f",
                        current.partition_key,
                        download_result.source_size_bytes,
                        download_result.duration_seconds,
                    )
                    execute_hostrada_partition(current, instance)

                    quality = query_hostrada_month_quality(current)
                    if not quality.passed:
                        raise RuntimeError(
                            "HOSTRADA SQL completeness check failed for "
                            f"{current.partition_key}; source files were retained"
                        )

                    cleanup = delete_verified_hostrada_sources(
                        current,
                        resolved_paths,
                    )
                    summary.completed_month_count += 1
                    summary.deleted_file_count += cleanup.deleted_file_count
                    summary.deleted_bytes += cleanup.bytes_deleted

                    LOGGER.info(
                        "hostrada_month_completed month=%s completed=%s "
                        "skipped=%s total=%s plr_rows=%s berlin_rows=%s "
                        "deleted_files=%s elapsed_seconds=%.2f",
                        current.partition_key,
                        summary.completed_month_count,
                        summary.skipped_month_count,
                        summary.requested_month_count,
                        quality.plr_hour_count,
                        quality.berlin_hour_count,
                        cleanup.deleted_file_count,
                        perf_counter() - started,
                    )

                    if following is None:
                        current = None
                    else:
                        if not prefetch:
                            next_download = submit_download(executor, following)
                        if next_download is None:
                            raise AssertionError("Missing next HOSTRADA download")
                        current = following
                        current_download = next_download
                        next_download = None

            except BaseException:
                stopped.set()
                if next_download is not None:
                    next_download.cancel()
                raise

    LOGGER.info(
        "hostrada_backfill_completed requested=%s completed=%s skipped=%s "
        "deleted_files=%s deleted_bytes=%s elapsed_seconds=%.2f",
        summary.requested_month_count,
        summary.completed_month_count,
        summary.skipped_month_count,
        summary.deleted_file_count,
        summary.deleted_bytes,
        perf_counter() - started,
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill monthly HOSTRADA weather with one bounded background "
            "download and quality-gated source deletion."
        )
    )
    parser.add_argument("--start", default="1995-01", metavar="YYYY-MM")
    parser.add_argument("--end", default="2025-12", metavar="YYYY-MM")
    parser.add_argument(
        "--min-free-gib",
        type=float,
        default=DEFAULT_MINIMUM_FREE_GIB,
        help="Stop before a download or transformation if less space is free.",
    )
    parser.add_argument(
        "--storage-path",
        action="append",
        default=[],
        metavar="PATH",
        help="Also enforce the free-space reserve on this filesystem.",
    )
    parser.add_argument(
        "--max-download-attempts",
        type=int,
        default=DEFAULT_DOWNLOAD_ATTEMPTS,
    )
    parser.add_argument(
        "--no-prefetch",
        action="store_true",
        help="Download each month only after the previous month was deleted.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    instance = None

    try:
        start = HostradaMonthKey.from_partition_key(args.start)
        end = HostradaMonthKey.from_partition_key(args.end)

        dagster_home = os.environ.get("DAGSTER_HOME")
        if not dagster_home:
            raise RuntimeError(
                "DAGSTER_HOME must point to persistent Dagster storage; "
                'run: export DAGSTER_HOME="$PWD/.dagster_home"'
            )
        Path(dagster_home).mkdir(parents=True, exist_ok=True)
        instance = dg.DagsterInstance.get()

        run_hostrada_backfill(
            start=start,
            end=end,
            instance=instance,
            minimum_free_gib=args.min_free_gib,
            storage_paths=tuple(Path(value) for value in args.storage_path),
            max_download_attempts=args.max_download_attempts,
            prefetch=not args.no_prefetch,
        )
        return 0

    except KeyboardInterrupt:
        LOGGER.error("hostrada_backfill_interrupted; verified months remain safe")
        return 130
    except Exception:
        LOGGER.exception("hostrada_backfill_failed; incomplete sources were retained")
        return 1
    finally:
        if instance is not None:
            instance.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
