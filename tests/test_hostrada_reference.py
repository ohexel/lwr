from types import SimpleNamespace

import dagster as dg
import pytest

from src.dagster_pipeline.assets.database_hostrada_reference import (
    HOSTRADA_REFERENCE_ASSETS,
)
from src.dagster_pipeline.jobs import HOSTRADA_REFERENCE_JOB
from src.dagster_pipeline.partitions import (
    HOSTRADA_MONTHLY_PARTITIONS,
    HOSTRADA_REFERENCE_PARTITIONS,
)
from src.hostrada_reference import (
    HOSTRADA_REFERENCE_CALENDAR_MONTHS,
    HOSTRADA_REFERENCE_END_YEAR,
    HOSTRADA_REFERENCE_START_YEAR,
    hostrada_reference_month_from_partition,
)
from src import hostrada_reference_build


def test_hostrada_reference_calendar_contract_is_fixed_and_explicit():
    assert HOSTRADA_REFERENCE_START_YEAR == 1995
    assert HOSTRADA_REFERENCE_END_YEAR == 2025
    assert HOSTRADA_REFERENCE_CALENDAR_MONTHS == tuple(
        f"{month:02d}" for month in range(1, 13)
    )
    assert hostrada_reference_month_from_partition("02") == 2
    assert hostrada_reference_month_from_partition("12") == 12


def test_hostrada_reference_partition_rejects_ambiguous_calendar_months():
    for invalid in ("2", "00", "13", "2025-02", ""):
        with pytest.raises(ValueError, match="calendar month"):
            hostrada_reference_month_from_partition(invalid)


def test_hostrada_reference_partitions_are_separate_from_utc_source_months():
    assert isinstance(HOSTRADA_REFERENCE_PARTITIONS, dg.StaticPartitionsDefinition)
    assert isinstance(HOSTRADA_MONTHLY_PARTITIONS, dg.MonthlyPartitionsDefinition)


def test_hostrada_reference_assets_expose_both_geographies_and_one_job():
    keys = {
        key.to_user_string()
        for asset in HOSTRADA_REFERENCE_ASSETS
        for key in asset.keys
    }

    assert keys == {
        "analytical/hostrada_plr_hourly_reference",
        "analytical/hostrada_berlin_hourly_reference",
    }
    assert all(
        asset.partitions_def is HOSTRADA_REFERENCE_PARTITIONS
        for asset in HOSTRADA_REFERENCE_ASSETS
    )
    assert HOSTRADA_REFERENCE_JOB.name == "hostrada_reference"


def test_hostrada_reference_build_skips_verified_calendar_months(monkeypatch):
    from src.dagster_pipeline import definitions

    completed = {"01"}
    executed = []

    class FakeJob:
        def execute_in_process(self, **kwargs):
            partition_key = kwargs["partition_key"]
            executed.append(partition_key)
            completed.add(partition_key)
            return SimpleNamespace(success=True, run_id=f"run-{partition_key}")

    monkeypatch.setattr(
        hostrada_reference_build,
        "reference_month_is_complete",
        lambda partition_key: partition_key in completed,
    )
    monkeypatch.setattr(
        definitions,
        "defs",
        SimpleNamespace(get_job_def=lambda name: FakeJob()),
    )

    summary = hostrada_reference_build.run_hostrada_reference_build(
        instance=object(),
        partition_keys=("01", "02", "03"),
    )

    assert executed == ["02", "03"]
    assert summary.requested_month_count == 3
    assert summary.completed_month_count == 2
    assert summary.skipped_month_count == 1


def test_hostrada_reference_build_preserves_previous_months_after_failure(
    monkeypatch,
):
    from src.dagster_pipeline import definitions

    completed = set()

    class FakeJob:
        def execute_in_process(self, **kwargs):
            partition_key = kwargs["partition_key"]
            if partition_key == "02":
                return SimpleNamespace(success=False, run_id="failed-02")
            completed.add(partition_key)
            return SimpleNamespace(success=True, run_id=f"run-{partition_key}")

    monkeypatch.setattr(
        hostrada_reference_build,
        "reference_month_is_complete",
        lambda partition_key: partition_key in completed,
    )
    monkeypatch.setattr(
        definitions,
        "defs",
        SimpleNamespace(get_job_def=lambda name: FakeJob()),
    )

    with pytest.raises(RuntimeError, match="calendar month 02"):
        hostrada_reference_build.run_hostrada_reference_build(
            instance=object(),
            partition_keys=("01", "02", "03"),
        )

    assert completed == {"01"}
