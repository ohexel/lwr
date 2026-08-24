"""Delete HOSTRADA sources only after their committed outputs pass SQL checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.database.connection import database_connection
from src.database.hostrada_state import query_hostrada_month_quality
from src.hostrada_contract import (
    HOSTRADA_GRID_CONTRACT,
    HOSTRADA_REQUIRED_VARIABLES,
    HostradaMonthKey,
)
from src.hostrada_paths import HostradaPaths


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HostradaSourceCleanup:
    source_month: str
    deleted_file_count: int
    bytes_deleted: int


def delete_verified_hostrada_sources(
    month: HostradaMonthKey,
    paths: HostradaPaths | None = None,
) -> HostradaSourceCleanup:
    """Keep provenance permanently; delete only this verified month's files."""
    quality = query_hostrada_month_quality(month)
    if not quality.passed:
        raise RuntimeError(
            "Refusing to delete HOSTRADA files for an incomplete month: "
            f"{month.partition_key}"
        )

    resolved_paths = paths or HostradaPaths()
    source_root = (
        resolved_paths.project_root / "data" / "raw" / "hostrada"
    ).resolve()
    deleted_files = 0
    deleted_bytes = 0

    with database_connection(
        application_name="capstone_hostrada_verified_source_cleanup"
    ) as connection:
        rows = connection.execute(
            """
            SELECT variable_name, source_path
            FROM raw.hostrada_month_source
            WHERE source_month_utc = %s::DATE
              AND source_grid_id = %s
            ORDER BY variable_name
            """,
            (
                month.start_utc.date(),
                HOSTRADA_GRID_CONTRACT.source_grid_id,
            ),
        ).fetchall()

        observed = {str(row[0]): str(row[1]) for row in rows}
        if set(observed) != set(HOSTRADA_REQUIRED_VARIABLES):
            raise RuntimeError(
                "Refusing to delete HOSTRADA files without exactly three "
                f"canonical manifest entries: {month.partition_key}"
            )

        targets = []
        for variable_name in HOSTRADA_REQUIRED_VARIABLES:
            expected = resolved_paths.source_file(month, variable_name)
            if observed[variable_name] != str(expected):
                raise RuntimeError(
                    "Refusing to delete a HOSTRADA file outside its canonical "
                    f"manifest path: {observed[variable_name]}"
                )
            if not expected.resolve().is_relative_to(source_root):
                raise RuntimeError(
                    f"Refusing to delete a HOSTRADA file outside {source_root}"
                )
            if expected.is_symlink():
                raise RuntimeError(f"Refusing to delete a symbolic link: {expected}")
            if expected.exists() and not expected.is_file():
                raise RuntimeError(f"HOSTRADA source is not a file: {expected}")
            targets.append((variable_name, expected))

        for variable_name, source_path in targets:
            if source_path.is_file():
                source_size = source_path.stat().st_size
                source_path.unlink()
                deleted_files += 1
                deleted_bytes += source_size

            connection.execute(
                """
                UPDATE raw.hostrada_month_source
                SET source_deleted_at_utc = COALESCE(
                    source_deleted_at_utc,
                    NOW()
                )
                WHERE source_month_utc = %s::DATE
                  AND variable_name = %s
                  AND source_grid_id = %s
                  AND source_deleted_at_utc IS NULL
                """,
                (
                    month.start_utc.date(),
                    variable_name,
                    HOSTRADA_GRID_CONTRACT.source_grid_id,
                ),
            )

    LOGGER.info(
        "hostrada_sources_deleted month=%s files=%s bytes=%s",
        month.partition_key,
        deleted_files,
        deleted_bytes,
    )
    return HostradaSourceCleanup(
        source_month=month.partition_key,
        deleted_file_count=deleted_files,
        bytes_deleted=deleted_bytes,
    )
