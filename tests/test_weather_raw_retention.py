from datetime import datetime, timezone
from pathlib import Path

from src.forecast_key import ProjectPaths
from src.retention.weather_raw import (
        prune_raw_weather_files,
        )

NOW_UTC = datetime(
        2026,
        8,
        23,
        10,
        0,
        tzinfo = timezone.utc,
        )

RETENTION_DAYS = 7


def _write_raw_partition(
        *,
        project_root: Path,
        indicator: str = "t_2m",
        run_label: str = "20260801T1200",
        lead_label: str = "PT000H00M",
        ) -> tuple[Path, Path]:
    """
    Create one fake retained GRIB and its acquisition sidecar.

    The layout matches the real raw-weather archive:

    data/raw/icon_d2_ruc/
        <indicator>/
            <run>/
                <lead>/
                    <indicator>.grib2
                    download_metadata.json
    """
    partition_dir = (
            project_root
            / "data"
            / "raw"
            / "icon_d2_ruc"
            / indicator
            / run_label
            / lead_label
            )

    partition_dir.mkdir(
            parents = True,
            exist_ok = True
            )

    grib_path = (
            partition_dir
            / f"{indicator}.grib2"
            )
    sidecar_path = (
            partition_dir
            / "download_metadata.json"
            )

    grib_path.write_bytes( b"fake-grub-content" )
    sidecar_path.write_text(
            '{"acquisition_status": "downloaded"}\n',
            encoding = "utf-8"
            )

    return grib_path, sidecar_path


def test_retention_apply_removes_grib_sidecar_and_empty_directores(
        tmp_path: Path,
        ) -> None:
    grib_path, sidecar_path = (
            _write_raw_partition( project_root = tmp_path )
            )

    lead_dir = grib_path.parent
    run_dir = lead_dir.parent
    indicator_dir = run_dir.parent
    weather_root = indicator_dir.parent

    result = prune_raw_weather_files(
            paths = ProjectPaths( project_root = tmp_path ),
            retention_days = RETENTION_DAYS,
            pinned_partitions = frozenset(),
            now_utc = NOW_UTC,
            dry_run = False
            )

    assert result.candidate_file_count == 1
    assert result.deleted_file_count == 1
    assert result.deleted_sidecar_file_count == 1
    assert result.retained_recent_file_count == 0
    assert result.retained_pinned_file_count == 0

    # Both members of the retained-source pair
    # must be gone.
    assert not grib_path.exists()
    assert not sidecar_path.exists()

    # Nothing else was in these directories, so
    # retention should prune all three.
    assert not lead_dir.exists()
    assert not run_dir.exists()
    assert not indicator_dir.exists()

    # But pruning must stop at the weather archive
    # root. Retention owns children of this directory,
    # not the root itself.
    assert weather_root.is_dir()


def test_retention_pin_preservers_grib_and_sidecar(
        tmp_path: Path,
        ) -> None:
    run_label = "20260801T1200"
    lead_label = "PT000H00M"

    grib_path, sidecar_path = (
            _write_raw_partition(
                project_root = tmp_path,
                run_label = run_label,
                lead_label = lead_label
                )
            )

    pinned_partition = (
            f"{run_label}/{lead_label}"
            )

    result = prune_raw_weather_files(
            paths = ProjectPaths(
                project_root = tmp_path
                ),
            retention_days = RETENTION_DAYS,
            pinned_partitions = frozenset(
                {pinned_partition}
                ),
            now_utc = NOW_UTC,
            dry_run = False
            )

    assert result.candidate_file_count == 1
    assert result.deleted_file_count == 0
    assert result.deleted_sidecar_file_count == 0
    assert result.retained_recent_file_count == 0
    assert result.retained_pinned_file_count == 1
    assert result.bytes_deleted == 0

    assert grib_path.is_file()
    assert sidecar_path.is_file()


def test_retention_dry_run_reports_grib_and_sidecar_without_deleting(
        tmp_path: Path
        ) -> None:
    grib_path, sidecar_path = (
            _write_raw_partition( project_root = tmp_path, )
            )

    grib_size = grib_path.stat().st_size
    sidecar_size = (sidecar_path.stat().st_size)

    result = prune_raw_weather_files(
            paths = ProjectPaths( project_root = tmp_path ),
            retention_days = RETENTION_DAYS,
            pinned_partitions = frozenset(),
            now_utc = NOW_UTC,
            dry_run = True
            )

    assert result.candidate_file_count == 1
    assert result.deleted_file_count == 1
    assert (
            result.deleted_sidecar_file_count
            == 1
            )
    assert (
            result.retained_recent_file_count
            == 0
            )
    assert (
            result.retained_pinned_file_count
            == 0
            )

    assert result.bytes_deleted == (
            grib_size + sidecar_size
            )

    # A dry run must not modify the filesystem.
    assert grib_path.is_file()
    assert sidecar_path.is_file()
    assert grib_path.parent.is_dir()
