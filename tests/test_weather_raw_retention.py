"""Protect the bounded, forecast-only operational retention contract."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.dagster_pipeline.assets import database_analytical
from src.dagster_pipeline.partitions import weather_partition_key
from src.forecast_key import ForecastKey, ProjectPaths
from src.retention.forecast_policy import (
    FORECAST_RETENTION_HOURS_ENV,
    forecast_partition_window_start,
    forecast_retention_cutoff,
    forecast_retention_hours,
)
from src.retention.weather_raw import (
    FORECAST_RETENTION_TABLES,
    prune_forecast_data,
    prune_forecast_database_rows,
    prune_raw_weather_files,
)


NOW_UTC = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
RETENTION_HOURS = 24


def _write_raw_partition(
    *,
    project_root: Path,
    indicator: str = "t_2m",
    run_label: str = "20260822T0900",
    lead_label: str = "PT000H00M",
) -> tuple[Path, Path]:
    partition_dir = (
        project_root
        / "data"
        / "raw"
        / "icon_d2_ruc"
        / indicator
        / run_label
        / lead_label
    )
    partition_dir.mkdir(parents=True, exist_ok=True)
    grib_path = partition_dir / f"{indicator}.grib2"
    sidecar_path = partition_dir / "download_metadata.json"
    grib_path.write_bytes(b"fake-grib-content")
    sidecar_path.write_text('{"acquisition_status": "downloaded"}\n')
    return grib_path, sidecar_path


@dataclass
class FakeCursor:
    rowcount: int

    def fetchone(self) -> tuple[int]:
        return (self.rowcount,)


class FakeConnection:
    def __init__(self, rows_per_table: int = 3) -> None:
        self.rows_per_table = rows_per_table
        self.statements: list[tuple[str, tuple[datetime]]] = []

    def execute(self, statement: str, parameters: tuple[datetime]) -> FakeCursor:
        self.statements.append((statement, parameters))
        return FakeCursor(rowcount=self.rows_per_table)


def _connection_factory(connection: FakeConnection):
    @contextmanager
    def connect(*, application_name: str):
        assert application_name == "capstone_forecast_retention"
        yield connection

    return connect


def test_retention_apply_removes_expired_grib_sidecar_and_empty_directories(
    tmp_path: Path,
) -> None:
    grib_path, sidecar_path = _write_raw_partition(project_root=tmp_path)
    lead_dir = grib_path.parent
    run_dir = lead_dir.parent
    indicator_dir = run_dir.parent
    forecast_root = indicator_dir.parent

    result = prune_raw_weather_files(
        paths=ProjectPaths(project_root=tmp_path),
        retention_hours=RETENTION_HOURS,
        now_utc=NOW_UTC,
        dry_run=False,
    )

    assert result.retention_hours == 24
    assert result.deleted_file_count == 1
    assert result.deleted_sidecar_file_count == 1
    assert result.retained_recent_file_count == 0
    assert not grib_path.exists()
    assert not sidecar_path.exists()
    assert not lead_dir.exists()
    assert not run_dir.exists()
    assert not indicator_dir.exists()
    assert forecast_root.is_dir()


def test_retention_preserves_run_exactly_at_24_hour_boundary(
    tmp_path: Path,
) -> None:
    expired_grib, _ = _write_raw_partition(
        project_root=tmp_path,
        run_label="20260822T0900",
    )
    boundary_grib, boundary_sidecar = _write_raw_partition(
        project_root=tmp_path,
        run_label="20260822T1000",
    )

    result = prune_raw_weather_files(
        paths=ProjectPaths(project_root=tmp_path),
        retention_hours=24,
        now_utc=NOW_UTC,
        dry_run=False,
    )

    assert result.deleted_file_count == 1
    assert result.retained_recent_file_count == 1
    assert not expired_grib.exists()
    assert boundary_grib.is_file()
    assert boundary_sidecar.is_file()


def test_retention_dry_run_reports_sources_without_deleting(
    tmp_path: Path,
) -> None:
    grib_path, sidecar_path = _write_raw_partition(project_root=tmp_path)

    result = prune_raw_weather_files(
        paths=ProjectPaths(project_root=tmp_path),
        retention_hours=24,
        now_utc=NOW_UTC,
        dry_run=True,
    )

    assert result.deleted_file_count == 1
    assert result.deleted_sidecar_file_count == 1
    assert result.bytes_deleted == grib_path.stat().st_size + sidecar_path.stat().st_size
    assert grib_path.is_file()
    assert sidecar_path.is_file()


@pytest.mark.parametrize("hours", [0, -1, 25, 48])
def test_retention_rejects_windows_outside_one_to_24_hours(hours: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 24 hours"):
        forecast_retention_hours(hours)


def test_retention_reads_bounded_hourly_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FORECAST_RETENTION_HOURS_ENV, "12")
    assert forecast_retention_hours() == 12
    assert forecast_retention_cutoff(now_utc=NOW_UTC) == NOW_UTC - timedelta(hours=12)


def test_partition_window_contains_at_most_24_whole_hour_runs() -> None:
    now = datetime(2026, 8, 23, 10, 37, tzinfo=timezone.utc)
    assert forecast_partition_window_start(now_utc=now, retention_hours=24) == (
        datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
    )
    assert forecast_partition_window_start(now_utc=now, retention_hours=1) == (
        datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
    )


def test_database_retention_dry_run_counts_forecast_tables_only() -> None:
    connection = FakeConnection(rows_per_table=4)

    result = prune_forecast_database_rows(
        retention_hours=24,
        now_utc=NOW_UTC,
        dry_run=True,
        connection_factory=_connection_factory(connection),
    )

    assert result.total_affected_rows == 4 * len(FORECAST_RETENTION_TABLES)
    assert list(result.affected_rows_by_table) == list(FORECAST_RETENTION_TABLES)
    assert all(statement.startswith("SELECT COUNT(*)") for statement, _ in connection.statements)
    assert all(
        parameters == (datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc),)
        for _, parameters in connection.statements
    )
    assert all("hostrada" not in table for table in FORECAST_RETENTION_TABLES)


def test_database_retention_deletes_children_before_forecast_parents() -> None:
    connection = FakeConnection(rows_per_table=2)

    result = prune_forecast_database_rows(
        retention_hours=24,
        now_utc=NOW_UTC,
        dry_run=False,
        connection_factory=_connection_factory(connection),
    )

    assert result.total_affected_rows == 2 * len(FORECAST_RETENTION_TABLES)
    assert all(statement.startswith("DELETE FROM") for statement, _ in connection.statements)
    assert FORECAST_RETENTION_TABLES.index("analytical.plr_weather_population") < (
        FORECAST_RETENTION_TABLES.index("analytical.plr_weather")
    )
    assert FORECAST_RETENTION_TABLES.index("raw.icon_d2_ruc_field") < (
        FORECAST_RETENTION_TABLES.index("raw.icon_d2_ruc_source")
    )


def test_forecast_retention_uses_identical_database_and_file_cutoffs(
    tmp_path: Path,
) -> None:
    _write_raw_partition(project_root=tmp_path)
    connection = FakeConnection(rows_per_table=1)

    result = prune_forecast_data(
        paths=ProjectPaths(project_root=tmp_path),
        retention_hours=24,
        now_utc=NOW_UTC,
        dry_run=True,
        connection_factory=_connection_factory(connection),
    )

    assert result.cutoff_utc == result.database.cutoff_utc
    assert result.cutoff_utc == result.raw_files.cutoff_utc
    assert result.database.total_affected_rows == len(FORECAST_RETENTION_TABLES)
    assert result.raw_files.deleted_file_count == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_automatic_cleanup_runs_only_after_a_successful_final_partition(
    monkeypatch: pytest.MonkeyPatch,
    accepted: bool,
) -> None:
    events: list[str] = []
    recorded_metadata: list[dict[str, object]] = []
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-23T10:00",
        lead_time="PT000H00M",
    )

    @contextmanager
    def fake_connection(*, application_name: str):
        assert application_name == "capstone_weather_population"
        events.append("refresh_started")

        class RefreshConnection:
            def execute(self, statement: str, parameters: object):
                del statement, parameters
                return SimpleNamespace(
                    fetchone=lambda: (accepted, 542, 540, 2, "2025-12-31", None)
                )

        yield RefreshConnection()
        events.append("refresh_committed")

    def fake_cleanup(*, dry_run: bool):
        assert not dry_run
        events.append("cleanup")
        return SimpleNamespace(
            retention_hours=24,
            database=SimpleNamespace(total_affected_rows=7),
            raw_files=SimpleNamespace(deleted_file_count=3),
        )

    monkeypatch.setattr(database_analytical, "database_connection", fake_connection)
    monkeypatch.setattr(database_analytical, "prune_forecast_data", fake_cleanup)
    context = SimpleNamespace(
        partition_key=weather_partition_key(forecast),
        add_output_metadata=recorded_metadata.append,
    )
    function = (
        database_analytical.analytical_plr_weather_population
        .node_def.compute_fn.decorated_fn
    )

    if accepted:
        function(context)
        assert events == ["refresh_started", "refresh_committed", "cleanup"]
        assert recorded_metadata[0]["forecast_retention_hours"] == 24
        assert recorded_metadata[0]["expired_forecast_rows_deleted"] == 7
        assert recorded_metadata[0]["expired_forecast_files_deleted"] == 3
    else:
        with pytest.raises(RuntimeError, match="rejected final analytical partition"):
            function(context)
        assert events == ["refresh_started", "refresh_committed"]
        assert recorded_metadata == []
