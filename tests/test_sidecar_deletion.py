def test_retention_dry_run_reports_grib_and_sidecar_without_deleting(
        tmp_path: Path
        ) -> None:
    grib_path, sidecar_path = (
            _write_raw_partition( project_root = tmp_path, )
            )

    grib_size = grib_path.stat().st_size
    sidecar_size = (sidecar_path.stat().st_size)

    result = prun_raw_weather_files(
            paths = ProjectPaths( project_root = tmp_path ),
            retention_days = RETENTION_DAYS,
            pinned_partitions = frozenset(),
            now_utc = NOW_UTC,
            dry_run = True
            )

    assert result.candidate_file_Count == 1
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

