"""Run or resume twelve independently committed HOSTRADA reference months."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import dagster as dg

from src.database.connection import database_connection
from src.database.spatial_state import current_geography_version
from src.hostrada_contract import HOSTRADA_GRID_CONTRACT
from src.hostrada_reference import (
    HOSTRADA_REFERENCE_CALENDAR_MONTHS,
    hostrada_reference_month_from_partition,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class HostradaReferenceBuildSummary:
    requested_month_count: int
    completed_month_count: int = 0
    skipped_month_count: int = 0


def reference_month_is_complete(partition_key: str) -> bool:
    calendar_month = hostrada_reference_month_from_partition(partition_key)

    with database_connection(
        application_name="capstone_hostrada_reference_checkpoint"
    ) as connection:
        geography_version = current_geography_version(connection)
        result = connection.execute(
            """
            SELECT quality.passed
            FROM analytical.check_hostrada_reference_month_quality(
                %s::INTEGER,
                %s::TEXT,
                %s::TEXT
            ) AS quality
            """,
            (
                calendar_month,
                geography_version,
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchone()

    if result is None:
        raise RuntimeError("HOSTRADA reference checkpoint returned no result")

    return bool(result[0])


def run_hostrada_reference_build(
    *,
    instance: dg.DagsterInstance,
    partition_keys: tuple[str, ...] = HOSTRADA_REFERENCE_CALENDAR_MONTHS,
    force: bool = False,
) -> HostradaReferenceBuildSummary:
    from src.dagster_pipeline.definitions import defs

    if not partition_keys:
        raise ValueError("At least one reference calendar month is required")

    for partition_key in partition_keys:
        hostrada_reference_month_from_partition(partition_key)

    summary = HostradaReferenceBuildSummary(
        requested_month_count=len(partition_keys)
    )
    started = perf_counter()
    job = defs.get_job_def("hostrada_reference")

    for partition_key in partition_keys:
        if not force and reference_month_is_complete(partition_key):
            summary.skipped_month_count += 1
            LOGGER.info(
                "hostrada_reference_month_skipped month=%s reason=verified_outputs "
                "skipped=%s total=%s",
                partition_key,
                summary.skipped_month_count,
                summary.requested_month_count,
            )
            continue

        LOGGER.info("hostrada_reference_month_started month=%s", partition_key)
        result = job.execute_in_process(
            partition_key=partition_key,
            instance=instance,
            raise_on_error=False,
            tags={"hostrada/reference": "1995-2025"},
        )
        if not result.success:
            raise RuntimeError(
                "Dagster HOSTRADA reference job failed for calendar month "
                f"{partition_key}; previously committed months remain intact"
            )

        if not reference_month_is_complete(partition_key):
            raise RuntimeError(
                "HOSTRADA reference quality check failed for calendar month "
                f"{partition_key}"
            )

        summary.completed_month_count += 1
        LOGGER.info(
            "hostrada_reference_month_completed month=%s completed=%s "
            "skipped=%s total=%s run_id=%s elapsed_seconds=%.2f",
            partition_key,
            summary.completed_month_count,
            summary.skipped_month_count,
            summary.requested_month_count,
            result.run_id,
            perf_counter() - started,
        )

    LOGGER.info(
        "hostrada_reference_build_completed requested=%s completed=%s "
        "skipped=%s elapsed_seconds=%.2f",
        summary.requested_month_count,
        summary.completed_month_count,
        summary.skipped_month_count,
        perf_counter() - started,
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build or resume Berlin-local HOSTRADA reference statistics "
            "one calendar month at a time."
        )
    )
    parser.add_argument(
        "--month",
        action="append",
        choices=HOSTRADA_REFERENCE_CALENDAR_MONTHS,
        metavar="MM",
        help="Build only this local calendar month; repeat for several months.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild selected months even when their current quality gate passes.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    arguments = _parse_args()
    instance = None

    try:
        dagster_directory = os.environ.get("DAGSTER_HOME")
        if not dagster_directory:
            raise RuntimeError(
                "DAGSTER_HOME must point to persistent Dagster storage; "
                'run: export DAGSTER_HOME="$PWD/.dagster_home"'
            )

        Path(dagster_directory).mkdir(parents=True, exist_ok=True)
        instance = dg.DagsterInstance.get()
        run_hostrada_reference_build(
            instance=instance,
            partition_keys=tuple(
                arguments.month or HOSTRADA_REFERENCE_CALENDAR_MONTHS
            ),
            force=arguments.force,
        )
        return 0

    except KeyboardInterrupt:
        LOGGER.error(
            "hostrada_reference_build_interrupted; committed months remain safe"
        )
        return 130
    except Exception:
        LOGGER.exception("hostrada_reference_build_failed")
        return 1
    finally:
        if instance is not None:
            instance.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
