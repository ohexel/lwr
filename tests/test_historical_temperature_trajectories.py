"""Protect optional historical-year extraction and the clean-room boundary."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import historical_temperature_trajectories as historical
from src import run_forecast_horizon as horizon_runner
from src.dagster_pipeline.partitions import WEATHER_LEAD_TIMES


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_TIME_UTC = datetime(2026, 8, 24, 16, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def execute(self, query, parameters=None):
        self.calls.append((query, parameters))
        return Result(next(self.rows))


def install_connection(monkeypatch, rows):
    connection = Connection(rows)

    @contextmanager
    def fake_database_connection(**kwargs):
        yield connection

    monkeypatch.setattr(historical, "database_connection", fake_database_connection)
    return connection


def test_canonical_and_additive_historical_definitions_remain_identical():
    additive = (
        PROJECT_ROOT / "sql" / "plr_temperature_history_25h.sql"
    ).read_text(encoding="utf-8")
    canonical = (PROJECT_ROOT / "sql" / "bootstrap_schema.sql").read_text(
        encoding="utf-8"
    )

    table_start = additive.index("CREATE TABLE IF NOT EXISTS")
    function_start = additive.index("CREATE OR REPLACE FUNCTION")
    view_start = additive.index("CREATE OR REPLACE VIEW")
    definitions = (
        additive[table_start:function_start].strip().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1
        ),
        additive[function_start:view_start].strip().replace(
            "CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1
        ),
        additive[view_start:].strip().replace(
            "CREATE OR REPLACE VIEW", "CREATE VIEW", 1
        ),
    )

    for definition in definitions:
        assert definition in canonical


def test_history_lookup_uses_both_leading_source_index_columns():
    source = (
        PROJECT_ROOT / "sql" / "plr_temperature_history_25h.sql"
    ).read_text(encoding="utf-8")

    assert "source.source_month_utc = target.source_month_utc" in source
    assert "source.valid_time_utc = target.historical_valid_time_utc" in source
    assert "CROSS JOIN LATERAL" in source
    assert "OFFSET 0" in source
    assert "generate_series(1995, 2025)" in source
    assert "UPDATE analytical.hostrada_plr_hourly_reference" not in source
    assert "DELETE FROM analytical.hostrada_plr_hourly_reference" not in source


def test_human_readable_names_exist_only_in_the_final_history_view():
    source = (
        PROJECT_ROOT / "sql" / "plr_temperature_history_25h.sql"
    ).read_text(encoding="utf-8")
    processing, serving = source.split(
        "CREATE OR REPLACE VIEW analytical.current_plr_temperature_history_25h",
        maxsplit=1,
    )

    assert "plr_name" not in processing
    assert "analytical.plr_display_name" not in processing
    assert "forecast.plr_name" in serving


def test_bootstrap_installs_optional_history_after_complete_forecast_views():
    bootstrap = (
        PROJECT_ROOT / "scripts" / "bootstrap_database.sh"
    ).read_text(encoding="utf-8")

    assert bootstrap.index("< sql/plr_temperature_forecast_25h.sql") < (
        bootstrap.index("< sql/plr_temperature_history_25h.sql")
    )


def test_existing_history_cache_is_extended_from_indexed_hostrada_observations():
    upgrade = (
        PROJECT_ROOT / "sql" / "plr_apparent_temperature_history_25h.sql"
    ).read_text(encoding="utf-8")
    bootstrap = (
        PROJECT_ROOT / "scripts" / "bootstrap_database.sh"
    ).read_text(encoding="utf-8")

    assert "source.source_month_utc = date_trunc" in upgrade
    assert "source.valid_time_utc = history.historical_valid_time_utc" in upgrade
    assert "source.apparent_temperature_shade_c" in upgrade
    assert "ALTER COLUMN historical_apparent_temperature_c SET NOT NULL" in upgrade
    assert bootstrap.index("< sql/plr_apparent_temperature_history_25h.sql") < (
        bootstrap.index("< sql/plr_temperature_history_25h.sql")
    )


@pytest.mark.parametrize("reused_existing", [False, True])
def test_refresh_reports_complete_optional_historical_contract(
    monkeypatch,
    reused_existing,
):
    connection = install_connection(
        monkeypatch,
        [(True,), (RUN_TIME_UTC,), (542, 31, 25, 420_050, reused_existing)],
    )

    result = historical.refresh_historical_trajectories()

    assert result["status"] == "ready"
    assert result["operation"] == (
        "already_current" if reused_existing else "refreshed"
    )
    assert result["historical_start_year"] == 1995
    assert result["historical_end_year"] == 2025
    assert result["historical_row_count"] == 420_050
    assert result["plotting_view"] == (
        "analytical.current_plr_temperature_history_25h"
    )
    assert connection.calls[-1][1] == (RUN_TIME_UTC,)


def test_refresh_rejects_missing_schema_before_querying_forecast(monkeypatch):
    connection = install_connection(monkeypatch, [(False,)])

    with pytest.raises(RuntimeError, match="bash scripts/bootstrap_database.sh"):
        historical.refresh_historical_trajectories()

    assert len(connection.calls) == 1


def test_refresh_requires_a_complete_current_horizon(monkeypatch):
    install_connection(monkeypatch, [(True,), None])

    with pytest.raises(RuntimeError, match="No complete 25-point forecast"):
        historical.refresh_historical_trajectories()


def test_refresh_rejects_a_noncurrent_forecast_run(monkeypatch):
    install_connection(monkeypatch, [(True,), (RUN_TIME_UTC,)])
    different_run = datetime(2026, 8, 24, 15, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError, match="current complete forecast horizon"):
        historical.refresh_historical_trajectories(different_run)


def test_refresh_rejects_incomplete_historical_trajectories(monkeypatch):
    install_connection(
        monkeypatch,
        [(True,), (RUN_TIME_UTC,), (542, 30, 25, 406_500, False)],
    )

    with pytest.raises(RuntimeError, match="years=30"):
        historical.refresh_historical_trajectories()


def test_forecast_runner_keeps_historical_extraction_opt_in(monkeypatch):
    run_label = datetime.now(timezone.utc).strftime("%Y%m%dT%H00")
    calls = []

    monkeypatch.setattr(horizon_runner, "ensure_horizon_views_installed", lambda: None)
    monkeypatch.setattr(
        horizon_runner,
        "weather_population_partition_complete",
        lambda forecast: True,
    )
    monkeypatch.setattr(
        horizon_runner,
        "validate_forecast_horizon",
        lambda local_time: {
            "plr_count": 542,
            "forecast_row_count": 13_550,
            "lead_hour_count": len(WEATHER_LEAD_TIMES),
            "summary_row_count": 542,
        },
    )
    monkeypatch.setattr(
        horizon_runner,
        "refresh_historical_trajectories",
        lambda run_time: calls.append(run_time) or {"historical_row_count": 420_050},
    )

    ordinary = horizon_runner.run_forecast_horizon(run_label)
    assert "historical_trajectories" not in ordinary
    assert calls == []

    extended = horizon_runner.run_forecast_horizon(
        run_label,
        historical_trajectories=True,
    )
    assert len(calls) == 1
    assert extended["historical_trajectories"]["historical_row_count"] == 420_050


def test_history_cli_explains_original_source_requirement(capsys):
    with pytest.raises(SystemExit) as observed:
        historical.main(["--help"])

    assert observed.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "original HOSTRADA hourly" in output
    assert "compact reference snapshot alone is insufficient" in output
    assert "UTC, not Berlin-local time" in output


@pytest.mark.parametrize("value", ["20260824T1630", "2026-08-24T1600"])
def test_history_cli_rejects_noncanonical_or_partial_hour_runs(value):
    with pytest.raises(ValueError):
        historical.parse_run_time_label(value)
