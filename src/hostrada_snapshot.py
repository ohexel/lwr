"""Verify, import, and independently validate the portable HOSTRADA snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.database.connection import DatabaseSettings, database_connection


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / "snapshots" / "hostrada-reference-1995-2025.manifest.json"
)
PLR_TABLE = "analytical.hostrada_plr_hourly_reference"
BERLIN_TABLE = "analytical.hostrada_berlin_hourly_reference"
REFERENCE_TABLES = (PLR_TABLE, BERLIN_TABLE)


@dataclass(frozen=True)
class SnapshotManifest:
    archive_filename: str
    archive_size_bytes: int
    archive_sha256: str
    geography_version: str
    plr_count: int
    sorted_plr_ids_sha256: str
    expected_plr_rows: int
    expected_berlin_rows: int
    expected_observation_count: int
    reference_start_year: int
    reference_end_year: int

    @classmethod
    def load(cls, path: Path = DEFAULT_MANIFEST_PATH) -> "SnapshotManifest":
        payload = json.loads(path.read_text(encoding="utf-8"))

        if payload.get("format_version") != 1:
            raise ValueError("Unsupported HOSTRADA snapshot manifest version")

        artifact = payload["artifact"]
        geography = payload["geography"]
        reference = payload["reference"]
        tables = payload["tables"]

        if artifact["format"] != "postgresql-custom-data-only":
            raise ValueError("HOSTRADA snapshot must be a PostgreSQL data-only archive")
        if reference["timezone"] != "Europe/Berlin":
            raise ValueError("HOSTRADA snapshot must use the Europe/Berlin timezone")
        if reference["february_29"] != "excluded":
            raise ValueError("HOSTRADA snapshot must exclude February 29")

        manifest = cls(
            archive_filename=str(artifact["filename"]),
            archive_size_bytes=int(artifact["size_bytes"]),
            archive_sha256=str(artifact["sha256"]),
            geography_version=str(geography["version"]),
            plr_count=int(geography["plr_count"]),
            sorted_plr_ids_sha256=str(geography["sorted_plr_ids_sha256"]),
            expected_plr_rows=int(tables[PLR_TABLE]["expected_row_count"]),
            expected_berlin_rows=int(tables[BERLIN_TABLE]["expected_row_count"]),
            expected_observation_count=int(
                reference["included_berlin_observation_count"]
            ),
            reference_start_year=int(reference["start_year"]),
            reference_end_year=int(reference["end_year"]),
        )

        if manifest.expected_plr_rows != (
            manifest.plr_count * manifest.expected_berlin_rows
        ):
            raise ValueError("HOSTRADA snapshot manifest row counts are inconsistent")
        if len(manifest.archive_sha256) != 64:
            raise ValueError("HOSTRADA snapshot manifest SHA-256 is invalid")

        return manifest


@dataclass(frozen=True)
class SnapshotQuality:
    passed: bool
    expected_plr_count: int
    installed_plr_count: int
    expected_calendar_hour_count: int
    expected_observation_count: int
    plr_reference_count: int
    berlin_reference_count: int
    plr_sample_count_failure_count: int
    berlin_sample_count_failure_count: int
    unexpected_plr_geography_count: int
    unexpected_berlin_geography_count: int
    statistic_order_failure_count: int

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "SnapshotQuality":
        return cls(bool(row[0]), *(int(value) for value in row[1:]))

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "passed": self.passed,
            "expected_plr_count": self.expected_plr_count,
            "installed_plr_count": self.installed_plr_count,
            "expected_calendar_hour_count": self.expected_calendar_hour_count,
            "expected_observation_count": self.expected_observation_count,
            "plr_reference_count": self.plr_reference_count,
            "berlin_reference_count": self.berlin_reference_count,
            "plr_sample_count_failure_count": self.plr_sample_count_failure_count,
            "berlin_sample_count_failure_count": (
                self.berlin_sample_count_failure_count
            ),
            "unexpected_plr_geography_count": self.unexpected_plr_geography_count,
            "unexpected_berlin_geography_count": (
                self.unexpected_berlin_geography_count
            ),
            "statistic_order_failure_count": self.statistic_order_failure_count,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def sorted_plr_ids_sha256(plr_ids: list[str]) -> str:
    canonical_ids = "".join(f"{plr_id}\n" for plr_id in sorted(plr_ids))
    return hashlib.sha256(canonical_ids.encode("utf-8")).hexdigest()


def verify_archive(archive_path: Path, manifest: SnapshotManifest) -> dict[str, Any]:
    if not archive_path.is_file():
        raise FileNotFoundError(f"HOSTRADA reference archive not found: {archive_path}")

    observed_size = archive_path.stat().st_size
    if observed_size != manifest.archive_size_bytes:
        raise ValueError(
            "HOSTRADA archive size does not match the manifest: "
            f"observed {observed_size:,}; expected {manifest.archive_size_bytes:,}"
        )

    observed_sha256 = sha256_file(archive_path)
    if observed_sha256 != manifest.archive_sha256:
        raise ValueError(
            "HOSTRADA archive SHA-256 does not match the manifest: "
            f"observed {observed_sha256}; expected {manifest.archive_sha256}"
        )

    return {
        "archive_path": str(archive_path),
        "size_bytes": observed_size,
        "sha256": observed_sha256,
        "reference_start_year": manifest.reference_start_year,
        "reference_end_year": manifest.reference_end_year,
    }


def verify_installed_geography(manifest: SnapshotManifest) -> dict[str, Any]:
    with database_connection(
        application_name="capstone_hostrada_snapshot_geography"
    ) as connection:
        rows = connection.execute(
            """
            SELECT plr_row.plr_id
            FROM normalized.plr AS plr_row
            WHERE plr_row.geography_version = %s
            ORDER BY plr_row.plr_id COLLATE "C"
            """,
            (manifest.geography_version,),
        ).fetchall()

    plr_ids = [str(row[0]) for row in rows]
    if len(plr_ids) != manifest.plr_count:
        raise RuntimeError(
            "Installed PLR geography is incompatible with the HOSTRADA snapshot: "
            f"observed {len(plr_ids):,}; expected {manifest.plr_count:,}"
        )

    observed_fingerprint = sorted_plr_ids_sha256(plr_ids)
    if observed_fingerprint != manifest.sorted_plr_ids_sha256:
        raise RuntimeError(
            "Installed PLR identifiers do not match the HOSTRADA snapshot: "
            f"observed {observed_fingerprint}; "
            f"expected {manifest.sorted_plr_ids_sha256}"
        )

    return {
        "geography_version": manifest.geography_version,
        "plr_count": len(plr_ids),
        "sorted_plr_ids_sha256": observed_fingerprint,
    }


def validate_snapshot(manifest: SnapshotManifest) -> SnapshotQuality:
    with database_connection(
        application_name="capstone_hostrada_snapshot_validation"
    ) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM analytical.check_hostrada_reference_snapshot(
                %s::TEXT,
                %s::INTEGER
            )
            """,
            (manifest.geography_version, manifest.plr_count),
        ).fetchone()

    if row is None:
        raise RuntimeError("HOSTRADA snapshot validation returned no result")

    quality = SnapshotQuality.from_row(row)
    if quality.expected_observation_count != manifest.expected_observation_count:
        raise RuntimeError(
            "HOSTRADA snapshot calendar does not match the reference manifest"
        )
    if not quality.passed:
        raise RuntimeError(
            "HOSTRADA snapshot validation failed: "
            + json.dumps(quality.as_dict(), sort_keys=True)
        )

    return quality


def installed_reference_counts() -> tuple[int, int]:
    with database_connection(
        application_name="capstone_hostrada_snapshot_counts"
    ) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM analytical.hostrada_plr_hourly_reference),
                (SELECT COUNT(*) FROM analytical.hostrada_berlin_hourly_reference)
            """
        ).fetchone()

    if row is None:
        raise RuntimeError("HOSTRADA snapshot row-count query returned no result")

    return int(row[0]), int(row[1])


def restore_archive(
    archive_path: Path,
    manifest: SnapshotManifest,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    verified = verify_archive(archive_path, manifest)
    geography = verify_installed_geography(manifest)
    existing_plr_rows, existing_berlin_rows = installed_reference_counts()

    if existing_plr_rows or existing_berlin_rows:
        quality = validate_snapshot(manifest)
        LOGGER.info("A complete HOSTRADA snapshot already exists; skipping restore")
        return {
            "status": "already_installed",
            "archive": verified,
            "geography": geography,
            "quality": quality.as_dict(),
        }

    settings = DatabaseSettings.from_env()
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "docker/postgres.yml",
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "--username",
        settings.user,
        "--dbname",
        settings.database,
        "--data-only",
        "--single-transaction",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--schema=analytical",
        "--table=hostrada_plr_hourly_reference",
        "--table=hostrada_berlin_hourly_reference",
    ]

    LOGGER.info(
        "Restoring %s PLR rows and %s Berlin rows in one PostgreSQL transaction",
        f"{manifest.expected_plr_rows:,}",
        f"{manifest.expected_berlin_rows:,}",
    )
    with archive_path.open("rb") as archive:
        subprocess.run(command, cwd=project_root, stdin=archive, check=True)

    quality = validate_snapshot(manifest)
    return {
        "status": "imported",
        "archive": verified,
        "geography": geography,
        "quality": quality.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify, import, or validate the portable HOSTRADA reference."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Reference snapshot manifest.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    verify_parser = commands.add_parser("verify", help="Verify archive bytes.")
    verify_parser.add_argument("--archive", type=Path, required=True)

    import_parser = commands.add_parser(
        "import", help="Restore and validate the reference tables."
    )
    import_parser.add_argument("--archive", type=Path, required=True)

    commands.add_parser(
        "validate", help="Validate installed references without historical sources."
    )

    arguments = parser.parse_args(argv)
    manifest = SnapshotManifest.load(arguments.manifest)

    if arguments.command == "verify":
        result = verify_archive(arguments.archive, manifest)
    elif arguments.command == "import":
        result = restore_archive(arguments.archive, manifest)
    else:
        verify_installed_geography(manifest)
        result = validate_snapshot(manifest).as_dict()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
