from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from src import hostrada_backfill
from src.hostrada_backfill import (
    backfill_process_lock,
    iter_hostrada_months,
    run_hostrada_backfill,
)
from src.hostrada_contract import HOSTRADA_REQUIRED_VARIABLES, HostradaMonthKey
from src.hostrada_paths import HostradaPaths
from src.ingestion.hostrada_download import (
    HostradaMonthDownload,
    HostradaSourceDownload,
)
from src.retention import hostrada_raw
from src.retention.hostrada_raw import (
    HostradaSourceCleanup,
    delete_verified_hostrada_sources,
)


def _quality(passed: bool):
    return SimpleNamespace(
        passed=passed,
        plr_hour_count=390240,
        berlin_hour_count=720,
    )


def _download_result(month: HostradaMonthKey, paths: HostradaPaths):
    sources = []
    for variable in HOSTRADA_REQUIRED_VARIABLES:
        target = paths.source_file(month, variable)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(variable.encode())
        sources.append(
            HostradaSourceDownload(
                variable_name=variable,
                source_path=target,
                source_url="https://example.invalid/source.nc",
                source_size_bytes=target.stat().st_size,
                downloaded=True,
                attempts=1,
            )
        )
    return HostradaMonthDownload(
        source_month=month.partition_key,
        sources=tuple(sources),
        duration_seconds=0.01,
    )


def test_hostrada_backfill_month_range_is_inclusive_and_validated():
    observed = [
        month.partition_key
        for month in iter_hostrada_months(
            HostradaMonthKey(1995, 11),
            HostradaMonthKey(1996, 2),
        )
    ]

    assert observed == ["1995-11", "1995-12", "1996-01", "1996-02"]

    with pytest.raises(ValueError, match="start must not be later"):
        list(
            iter_hostrada_months(
                HostradaMonthKey(1996, 1),
                HostradaMonthKey(1995, 12),
            )
        )


def test_hostrada_backfill_overlaps_only_the_next_month(
    tmp_path: Path,
    monkeypatch,
):
    paths = HostradaPaths(tmp_path)
    completed = set()
    next_download_started = Event()
    executions = []

    monkeypatch.setattr(hostrada_backfill, "verify_backfill_prerequisites", lambda: None)
    monkeypatch.setattr(
        hostrada_backfill,
        "query_hostrada_month_quality",
        lambda month: _quality(month.partition_key in completed),
    )

    def fake_download(month, resolved_paths, **kwargs):
        del kwargs
        result = _download_result(month, resolved_paths)
        assert len(list(tmp_path.rglob("*.nc"))) <= 6
        if month.partition_key == "1995-02":
            next_download_started.set()
        return result

    def fake_execute(month, instance):
        del instance
        if month.partition_key == "1995-01":
            assert next_download_started.wait(timeout=2)
        executions.append(month.partition_key)
        completed.add(month.partition_key)

    def fake_cleanup(month, resolved_paths):
        assert month.partition_key in completed
        for variable in HOSTRADA_REQUIRED_VARIABLES:
            resolved_paths.source_file(month, variable).unlink()
        return HostradaSourceCleanup(month.partition_key, 3, 12)

    monkeypatch.setattr(hostrada_backfill, "download_hostrada_month", fake_download)
    monkeypatch.setattr(hostrada_backfill, "execute_hostrada_partition", fake_execute)
    monkeypatch.setattr(
        hostrada_backfill,
        "delete_verified_hostrada_sources",
        fake_cleanup,
    )

    summary = run_hostrada_backfill(
        start=HostradaMonthKey(1995, 1),
        end=HostradaMonthKey(1995, 2),
        instance=object(),
        paths=paths,
        minimum_free_gib=0,
    )

    assert executions == ["1995-01", "1995-02"]
    assert summary.completed_month_count == 2
    assert summary.deleted_file_count == 6
    assert not list(tmp_path.rglob("*.nc"))


def test_hostrada_backfill_skips_verified_months_and_resumes(
    tmp_path: Path,
    monkeypatch,
):
    paths = HostradaPaths(tmp_path)
    completed = {"1995-01"}
    downloaded = []
    _download_result(HostradaMonthKey(1995, 1), paths)

    monkeypatch.setattr(hostrada_backfill, "verify_backfill_prerequisites", lambda: None)
    monkeypatch.setattr(
        hostrada_backfill,
        "query_hostrada_month_quality",
        lambda month: _quality(month.partition_key in completed),
    )

    def fake_download(month, resolved_paths, **kwargs):
        del kwargs
        downloaded.append(month.partition_key)
        return _download_result(month, resolved_paths)

    def fake_execute(month, instance):
        del instance
        completed.add(month.partition_key)

    def fake_cleanup(month, resolved_paths):
        deleted = 0
        for variable in HOSTRADA_REQUIRED_VARIABLES:
            target = resolved_paths.source_file(month, variable)
            if target.exists():
                target.unlink()
                deleted += 1
        return HostradaSourceCleanup(month.partition_key, deleted, 0)

    monkeypatch.setattr(hostrada_backfill, "download_hostrada_month", fake_download)
    monkeypatch.setattr(hostrada_backfill, "execute_hostrada_partition", fake_execute)
    monkeypatch.setattr(
        hostrada_backfill,
        "delete_verified_hostrada_sources",
        fake_cleanup,
    )

    summary = run_hostrada_backfill(
        start=HostradaMonthKey(1995, 1),
        end=HostradaMonthKey(1995, 2),
        instance=object(),
        paths=paths,
        minimum_free_gib=0,
    )

    assert downloaded == ["1995-02"]
    assert summary.skipped_month_count == 1
    assert summary.completed_month_count == 1
    assert summary.deleted_file_count == 6
    assert not list(tmp_path.rglob("*.nc"))


def test_hostrada_backfill_can_disable_source_prefetch(
    tmp_path: Path,
    monkeypatch,
):
    paths = HostradaPaths(tmp_path)
    completed = set()

    monkeypatch.setattr(hostrada_backfill, "verify_backfill_prerequisites", lambda: None)
    monkeypatch.setattr(
        hostrada_backfill,
        "query_hostrada_month_quality",
        lambda month: _quality(month.partition_key in completed),
    )
    monkeypatch.setattr(
        hostrada_backfill,
        "download_hostrada_month",
        lambda month, resolved_paths, **kwargs: _download_result(
            month,
            resolved_paths,
        ),
    )

    def fake_execute(month, instance):
        del instance
        if month.partition_key == "1995-01":
            following = HostradaMonthKey(1995, 2)
            assert not paths.source_file(following, "tas").exists()
        completed.add(month.partition_key)

    def fake_cleanup(month, resolved_paths):
        for variable in HOSTRADA_REQUIRED_VARIABLES:
            resolved_paths.source_file(month, variable).unlink()
        return HostradaSourceCleanup(month.partition_key, 3, 12)

    monkeypatch.setattr(hostrada_backfill, "execute_hostrada_partition", fake_execute)
    monkeypatch.setattr(
        hostrada_backfill,
        "delete_verified_hostrada_sources",
        fake_cleanup,
    )

    summary = run_hostrada_backfill(
        start=HostradaMonthKey(1995, 1),
        end=HostradaMonthKey(1995, 2),
        instance=object(),
        paths=paths,
        minimum_free_gib=0,
        prefetch=False,
    )

    assert summary.completed_month_count == 2


def test_hostrada_prefetch_failure_preserves_the_completed_previous_month(
    tmp_path: Path,
    monkeypatch,
):
    paths = HostradaPaths(tmp_path)
    completed = set()

    monkeypatch.setattr(hostrada_backfill, "verify_backfill_prerequisites", lambda: None)
    monkeypatch.setattr(
        hostrada_backfill,
        "query_hostrada_month_quality",
        lambda month: _quality(month.partition_key in completed),
    )

    def fake_download(month, resolved_paths, **kwargs):
        del kwargs
        if month.partition_key == "1995-02":
            raise RuntimeError("February download failed")
        return _download_result(month, resolved_paths)

    def fake_execute(month, instance):
        del instance
        completed.add(month.partition_key)

    def fake_cleanup(month, resolved_paths):
        for variable in HOSTRADA_REQUIRED_VARIABLES:
            resolved_paths.source_file(month, variable).unlink()
        return HostradaSourceCleanup(month.partition_key, 3, 12)

    monkeypatch.setattr(hostrada_backfill, "download_hostrada_month", fake_download)
    monkeypatch.setattr(hostrada_backfill, "execute_hostrada_partition", fake_execute)
    monkeypatch.setattr(
        hostrada_backfill,
        "delete_verified_hostrada_sources",
        fake_cleanup,
    )

    with pytest.raises(RuntimeError, match="February download failed"):
        run_hostrada_backfill(
            start=HostradaMonthKey(1995, 1),
            end=HostradaMonthKey(1995, 2),
            instance=object(),
            paths=paths,
            minimum_free_gib=0,
        )

    assert completed == {"1995-01"}
    assert not list(tmp_path.rglob("*.nc"))


def test_hostrada_backfill_keeps_files_when_sql_quality_fails(
    tmp_path: Path,
    monkeypatch,
):
    paths = HostradaPaths(tmp_path)
    month = HostradaMonthKey(1995, 1)
    cleanup_calls = []

    monkeypatch.setattr(hostrada_backfill, "verify_backfill_prerequisites", lambda: None)
    monkeypatch.setattr(
        hostrada_backfill,
        "query_hostrada_month_quality",
        lambda observed_month: _quality(False),
    )
    monkeypatch.setattr(
        hostrada_backfill,
        "download_hostrada_month",
        lambda observed_month, resolved_paths, **kwargs: _download_result(
            observed_month,
            resolved_paths,
        ),
    )
    monkeypatch.setattr(
        hostrada_backfill,
        "execute_hostrada_partition",
        lambda observed_month, instance: None,
    )
    monkeypatch.setattr(
        hostrada_backfill,
        "delete_verified_hostrada_sources",
        lambda observed_month, resolved_paths: cleanup_calls.append(observed_month),
    )

    with pytest.raises(RuntimeError, match="SQL completeness check failed"):
        run_hostrada_backfill(
            start=month,
            end=month,
            instance=object(),
            paths=paths,
            minimum_free_gib=0,
        )

    assert cleanup_calls == []
    for variable in HOSTRADA_REQUIRED_VARIABLES:
        assert paths.source_file(month, variable).is_file()


def test_hostrada_cleanup_rejects_unverified_outputs(
    tmp_path: Path,
    monkeypatch,
):
    month = HostradaMonthKey(1995, 1)
    paths = HostradaPaths(tmp_path)
    _download_result(month, paths)
    monkeypatch.setattr(
        hostrada_raw,
        "query_hostrada_month_quality",
        lambda observed_month: _quality(False),
    )

    with pytest.raises(RuntimeError, match="Refusing to delete"):
        delete_verified_hostrada_sources(month, paths)

    assert len(list(tmp_path.rglob("*.nc"))) == 3


def test_hostrada_cleanup_preserves_unrelated_diagnostic_sources(
    tmp_path: Path,
    monkeypatch,
):
    month = HostradaMonthKey(1995, 1)
    diagnostic = HostradaMonthKey(2026, 6)
    paths = HostradaPaths(tmp_path)
    _download_result(month, paths)
    _download_result(diagnostic, paths)
    updates = []

    class FakeResult:
        def fetchall(self):
            return [
                (variable, str(paths.source_file(month, variable)))
                for variable in HOSTRADA_REQUIRED_VARIABLES
            ]

    class FakeConnection:
        def execute(self, statement, parameters=()):
            if "UPDATE raw.hostrada_month_source" in statement:
                updates.append(parameters)
            return FakeResult()

    @contextmanager
    def fake_connection(**kwargs):
        del kwargs
        yield FakeConnection()

    monkeypatch.setattr(
        hostrada_raw,
        "query_hostrada_month_quality",
        lambda observed_month: _quality(True),
    )
    monkeypatch.setattr(hostrada_raw, "database_connection", fake_connection)

    result = delete_verified_hostrada_sources(month, paths)

    assert result.deleted_file_count == 3
    assert len(updates) == 3
    for variable in HOSTRADA_REQUIRED_VARIABLES:
        assert not paths.source_file(month, variable).exists()
        assert paths.source_file(diagnostic, variable).is_file()


def test_hostrada_backfill_rejects_another_active_supervisor(
    tmp_path: Path,
):
    paths = HostradaPaths(tmp_path)

    with backfill_process_lock(paths):
        with pytest.raises(RuntimeError, match="Another HOSTRADA backfill"):
            with backfill_process_lock(paths):
                pass
