"""Initialize an operational installation without rebuilding HOSTRADA history."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import dagster as dg

from src.database.connection import DatabaseSettings, database_connection
from src.database.weather_mask import weather_mask_buffer_m
from src.hostrada_snapshot import (
    DEFAULT_MANIFEST_PATH,
    SnapshotManifest,
    restore_archive,
    verify_archive,
)
from src.icon_grid_contract import ICON_D2_GRID_CONTRACT
from src.static_snapshot import (
    STATIC_SOURCE_PATHS,
    read_static_manifest,
    restore_static_source,
)


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class StaticState:
    plr_count: int
    raw_population_count: int
    accepted_population_count: int
    rejected_population_count: int
    icon_cell_count: int
    bridge_row_count: int
    mask_cell_count: int

    def is_complete(self, expected_plr_count: int) -> bool:
        return (
            self.plr_count == expected_plr_count
            and self.raw_population_count == expected_plr_count
            and (
                self.accepted_population_count
                + self.rejected_population_count
            )
            == expected_plr_count
            and self.icon_cell_count == ICON_D2_GRID_CONTRACT.cell_count
            and self.bridge_row_count > 0
            and self.mask_cell_count > 0
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "plr_count": self.plr_count,
            "raw_population_count": self.raw_population_count,
            "accepted_population_count": self.accepted_population_count,
            "rejected_population_count": self.rejected_population_count,
            "icon_cell_count": self.icon_cell_count,
            "bridge_row_count": self.bridge_row_count,
            "mask_cell_count": self.mask_cell_count,
        }


def validate_static_state(manifest: SnapshotManifest) -> dict[str, bool]:
    with database_connection(
        application_name="capstone_operational_static_quality"
    ) as connection:
        sources = connection.execute(
            """
            SELECT
                (
                    SELECT source_sha256
                    FROM raw.lor_plr
                    GROUP BY source_sha256
                    ORDER BY MAX(loaded_at_utc) DESC
                    LIMIT 1
                ),
                (
                    SELECT source_sha256
                    FROM raw.afs_population
                    GROUP BY source_sha256
                    ORDER BY MAX(loaded_at_utc) DESC
                    LIMIT 1
                )
            """
        ).fetchone()

        if sources is None or sources[0] is None or sources[1] is None:
            raise RuntimeError("Static source provenance is incomplete")

        plr_quality = connection.execute(
            """
            SELECT passed
            FROM normalized.check_plr_geometry_quality(%s, %s)
            """,
            (sources[0], manifest.plr_count),
        ).fetchone()
        population_quality = connection.execute(
            """
            SELECT passed
            FROM normalized.check_population_quality(%s)
            """,
            (sources[1],),
        ).fetchone()
        icon_quality = connection.execute(
            """
            SELECT passed
            FROM normalized.check_icon_geometry_quality(%s, %s, %s)
            """,
            (
                ICON_D2_GRID_CONTRACT.source_grid_id,
                ICON_D2_GRID_CONTRACT.vertex_count,
                ICON_D2_GRID_CONTRACT.cell_count,
            ),
        ).fetchone()
        bridge_quality = connection.execute(
            """
            SELECT passed
            FROM normalized.check_icon_plr_area_bridge_quality(%s, %s, %s)
            """,
            (
                manifest.geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
                manifest.plr_count,
            ),
        ).fetchone()
        mask_quality = connection.execute(
            """
            SELECT passed
            FROM normalized.check_icon_weather_mask_quality(%s, %s, %s, %s)
            """,
            (
                manifest.geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
                weather_mask_buffer_m(),
                manifest.plr_count,
            ),
        ).fetchone()

    results = {
        "plr_geometry": bool(plr_quality and plr_quality[0]),
        "population": bool(population_quality and population_quality[0]),
        "icon_geometry": bool(icon_quality and icon_quality[0]),
        "icon_plr_area_bridge": bool(bridge_quality and bridge_quality[0]),
        "icon_weather_mask": bool(mask_quality and mask_quality[0]),
    }
    failures = [name for name, passed in results.items() if not passed]
    if failures:
        raise RuntimeError(
            "Operational static quality checks failed: " + ", ".join(failures)
        )

    return results


def initialize_database(project_root: Path = PROJECT_ROOT) -> None:
    LOGGER.info("Checking and initializing the canonical PostgreSQL schema")
    subprocess.run(
        ["bash", "scripts/bootstrap_database.sh"],
        cwd=project_root,
        check=True,
    )


def query_static_state(manifest: SnapshotManifest) -> StaticState:
    with database_connection(
        application_name="capstone_operational_static_state"
    ) as connection:
        row = connection.execute(
            """
            WITH latest_population_source AS MATERIALIZED (
                SELECT source_sha256
                FROM raw.afs_population
                GROUP BY source_sha256
                ORDER BY MAX(loaded_at_utc) DESC
                LIMIT 1
            )
            SELECT
                (
                    SELECT COUNT(*)
                    FROM normalized.plr
                    WHERE geography_version = %s
                ),
                (
                    SELECT COUNT(*)
                    FROM raw.afs_population
                    WHERE source_sha256 = (
                        SELECT source_sha256
                        FROM latest_population_source
                    )
                ),
                (
                    SELECT COUNT(*)
                    FROM normalized.plr_population_65plus
                    WHERE source_sha256 = (
                        SELECT source_sha256
                        FROM latest_population_source
                    )
                ),
                (
                    SELECT COUNT(*)
                    FROM normalized.plr_population_rejected
                    WHERE source_sha256 = (
                        SELECT source_sha256
                        FROM latest_population_source
                    )
                ),
                (
                    SELECT COUNT(*)
                    FROM normalized.icon_cell
                    WHERE source_grid_id = %s
                ),
                (
                    SELECT COUNT(*)
                    FROM normalized.icon_plr_area_bridge
                    WHERE geography_version = %s
                      AND source_grid_id = %s
                ),
                (
                    SELECT COUNT(*)
                    FROM normalized.icon_weather_mask
                    WHERE geography_version = %s
                      AND source_grid_id = %s
                      AND mask_buffer_m = %s
                )
            """,
            (
                manifest.geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
                manifest.geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
                manifest.geography_version,
                ICON_D2_GRID_CONTRACT.source_grid_id,
                weather_mask_buffer_m(),
            ),
        ).fetchone()

    if row is None:
        raise RuntimeError("Operational static-state query returned no result")

    return StaticState(*(int(value) for value in row))


def prepare_static_source(
    source_name: str,
    *,
    project_root: Path = PROJECT_ROOT,
    static_snapshot_path: Path | None = None,
    offline: bool = False,
) -> Path:
    target_path = project_root / STATIC_SOURCE_PATHS[source_name]
    if target_path.is_file():
        LOGGER.info("Reusing existing %s input: %s", source_name, target_path)
        return target_path

    if offline:
        if static_snapshot_path is not None:
            return restore_static_source(
                static_snapshot_path,
                source_name,
                project_root=project_root,
            )

        if source_name == "population":
            from src.download_afs_population import (
                BUNDLED_FALLBACK_PATH,
                restore_bundled_csv,
            )

            restore_bundled_csv(target_path, BUNDLED_FALLBACK_PATH)
            LOGGER.info("Restored the verified bundled AfS population CSV")
            return target_path

        raise RuntimeError(
            f"Offline bootstrap requires --static-snapshot for {source_name}"
        )

    modules = {
        "lor_plr": "src.download_lor_wfs",
        "population": "src.download_afs_population",
        "icon_grid": "src.download_icon_d2_grid",
    }
    command = [sys.executable, "-m", modules[source_name]]

    LOGGER.info("Acquiring %s from its official source", source_name)
    try:
        subprocess.run(command, cwd=project_root, check=True)
    except subprocess.CalledProcessError:
        if static_snapshot_path is None:
            raise

        LOGGER.warning(
            "Automatic acquisition failed for %s; using the static snapshot",
            source_name,
        )
        return restore_static_source(
            static_snapshot_path,
            source_name,
            project_root=project_root,
        )

    if not target_path.is_file():
        raise RuntimeError(
            f"{modules[source_name]} succeeded but did not create {target_path}"
        )

    return target_path


def ensure_dagster_home(project_root: Path = PROJECT_ROOT) -> Path:
    configured_home = os.environ.get("DAGSTER_HOME")
    dagster_home = (
        Path(configured_home).expanduser()
        if configured_home
        else project_root / ".dagster_home"
    )
    if not dagster_home.is_absolute():
        dagster_home = (project_root / dagster_home).resolve()

    dagster_home.mkdir(parents=True, exist_ok=True)
    os.environ["DAGSTER_HOME"] = str(dagster_home)
    return dagster_home


def materialize_static_inputs(
    manifest: SnapshotManifest,
    *,
    project_root: Path = PROJECT_ROOT,
    static_snapshot_path: Path | None = None,
    offline: bool = False,
) -> StaticState:
    initial_state = query_static_state(manifest)
    if initial_state.is_complete(manifest.plr_count):
        validate_static_state(manifest)
        LOGGER.info("Operational static inputs are already materialized")
        return initial_state

    for source_name in STATIC_SOURCE_PATHS:
        prepare_static_source(
            source_name,
            project_root=project_root,
            static_snapshot_path=static_snapshot_path,
            offline=offline,
        )

    LOGGER.info(
        "Materializing %s PLRs, population, %s ICON cells, %s topology rows, "
        "the area bridge, and the weather mask",
        f"{manifest.plr_count:,}",
        f"{ICON_D2_GRID_CONTRACT.cell_count:,}",
        f"{ICON_D2_GRID_CONTRACT.topology_row_count:,}",
    )
    LOGGER.info(
        "ICON grid initialization is the main one-time operational bootstrap cost"
    )
    ensure_dagster_home(project_root)

    from src.dagster_pipeline.definitions import defs

    instance = dg.DagsterInstance.get()
    try:
        result = defs.get_job_def(
            "operational_static_bootstrap"
        ).execute_in_process(
            instance=instance,
            raise_on_error=False,
        )
    finally:
        instance.dispose()

    if not result.success:
        raise RuntimeError(
            "Operational static Dagster job failed; inspect its run logs and retry"
        )

    final_state = query_static_state(manifest)
    if not final_state.is_complete(manifest.plr_count):
        raise RuntimeError(
            "Operational static bootstrap produced incomplete state: "
            + json.dumps(final_state.as_dict(), sort_keys=True)
        )

    validate_static_state(manifest)
    return final_state


def bootstrap(
    reference_archive: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    static_snapshot_path: Path | None = None,
    offline: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    started_at = perf_counter()
    DatabaseSettings.from_env()
    manifest = SnapshotManifest.load(manifest_path)

    LOGGER.info("Verifying the HOSTRADA archive before expensive static processing")
    verify_archive(reference_archive, manifest)

    if static_snapshot_path is not None:
        read_static_manifest(static_snapshot_path)

    initialize_database(project_root)
    static_state = materialize_static_inputs(
        manifest,
        project_root=project_root,
        static_snapshot_path=static_snapshot_path,
        offline=offline,
    )
    snapshot_result = restore_archive(
        reference_archive,
        manifest,
        project_root=project_root,
    )

    return {
        "status": "ready",
        "duration_seconds": round(perf_counter() - started_at, 3),
        "dagster_home": str(ensure_dagster_home(project_root)),
        "static_state": static_state.as_dict(),
        "hostrada_snapshot": snapshot_result,
        "serving_view": "analytical.current_plr_weather_context",
        "weather_sensor_default_status": "STOPPED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize the operational Berlin weather pipeline without "
            "downloading or rebuilding HOSTRADA historical observations."
        )
    )
    parser.add_argument(
        "--reference-archive",
        type=Path,
        required=True,
        help="Verified PostgreSQL custom-format HOSTRADA reference archive.",
    )
    parser.add_argument(
        "--reference-manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Manifest describing the reference archive and PLR geography.",
    )
    parser.add_argument(
        "--static-snapshot",
        type=Path,
        help="Optional verified fallback archive for LOR, population, and ICON grid.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use static snapshot sources directly instead of attempting downloads.",
    )
    arguments = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    result = bootstrap(
        arguments.reference_archive,
        manifest_path=arguments.reference_manifest,
        static_snapshot_path=arguments.static_snapshot,
        offline=arguments.offline,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
