"""Protect the 25-point forecast runner and its source-independent serving contract."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from src import run_forecast_horizon as horizon_runner
from src.dagster_pipeline.partitions import WEATHER_LEAD_TIMES


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def current_run_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H00")


def test_canonical_and_additive_forecast_views_share_identical_definitions():
    additive = (
        PROJECT_ROOT / "sql" / "plr_temperature_forecast_25h.sql"
    ).read_text(encoding="utf-8")
    canonical = (PROJECT_ROOT / "sql" / "bootstrap_schema.sql").read_text(
        encoding="utf-8"
    )

    definitions = re.findall(
        r"CREATE OR REPLACE VIEW analytical\.current_plr_temperature_.*?;",
        additive,
        flags=re.DOTALL,
    )

    assert len(definitions) == 2
    for definition in definitions:
        assert definition.replace("CREATE OR REPLACE VIEW", "CREATE VIEW", 1) in canonical


def test_forecast_serving_does_not_query_historical_hourly_observations():
    serving_sql = (
        PROJECT_ROOT / "sql" / "plr_temperature_forecast_25h.sql"
    ).read_text(encoding="utf-8")

    assert "analytical.plr_weather_context" in serving_sql
    assert "plr_temperature_median_c" in serving_sql
    assert not re.search(
        r"\banalytical\.hostrada_(?:plr|berlin)_hourly\b",
        serving_sql,
    )


def test_bootstrap_installs_forecast_views_after_analyst_names():
    bootstrap_script = (
        PROJECT_ROOT / "scripts" / "bootstrap_database.sh"
    ).read_text(encoding="utf-8")

    assert bootstrap_script.index("< sql/plr_display_names.sql") < (
        bootstrap_script.index("< sql/plr_temperature_forecast_25h.sql")
    )


def test_horizon_runner_resumes_completed_leads_and_runs_missing_leads_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized = []
    expected_complete = {"PT000H00M", "PT006H00M", "PT024H00M"}

    monkeypatch.setattr(horizon_runner, "ensure_horizon_views_installed", lambda: None)
    monkeypatch.setattr(
        horizon_runner,
        "weather_population_partition_complete",
        lambda forecast: forecast.lead_time_label in expected_complete,
    )
    monkeypatch.setattr(
        horizon_runner,
        "run_forecast",
        lambda run_time, lead_time, **kwargs: materialized.append(lead_time),
    )
    monkeypatch.setattr(
        horizon_runner,
        "validate_forecast_horizon",
        lambda local_time: {
            "plr_count": 542,
            "forecast_row_count": 13_550,
            "lead_hour_count": 25,
            "summary_row_count": 542,
        },
    )

    result = horizon_runner.run_forecast_horizon(current_run_label())

    assert materialized == [
        lead_time for lead_time in WEATHER_LEAD_TIMES if lead_time not in expected_complete
    ]
    assert result["status"] == "ready"
    assert result["materialized_count"] == 22
    assert result["already_complete_count"] == 3
    assert result["quality"]["forecast_row_count"] == 13_550
    assert result["run_time_berlin"].endswith(("+01:00", "+02:00"))


def test_horizon_runner_stops_at_failed_lead_without_reporting_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized = []

    monkeypatch.setattr(horizon_runner, "ensure_horizon_views_installed", lambda: None)
    monkeypatch.setattr(
        horizon_runner,
        "weather_population_partition_complete",
        lambda forecast: False,
    )

    def fail_at_lead_three(run_time, lead_time, **kwargs):
        materialized.append(lead_time)
        if lead_time == "PT003H00M":
            raise RuntimeError("fixture partition failed")

    monkeypatch.setattr(horizon_runner, "run_forecast", fail_at_lead_three)
    monkeypatch.setattr(
        horizon_runner,
        "validate_forecast_horizon",
        lambda local_time: pytest.fail("Incomplete horizons must not be validated"),
    )

    with pytest.raises(RuntimeError, match="fixture partition failed"):
        horizon_runner.run_forecast_horizon(current_run_label())

    assert materialized == [
        "PT000H00M",
        "PT001H00M",
        "PT002H00M",
        "PT003H00M",
    ]


def test_missing_serving_views_fail_before_forecast_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        def execute(self, query, parameters):
            return self

        def fetchone(self):
            return (False,)

    @contextmanager
    def fake_database_connection(**kwargs):
        yield Connection()

    monkeypatch.setattr(horizon_runner, "database_connection", fake_database_connection)
    monkeypatch.setattr(
        horizon_runner,
        "weather_population_partition_complete",
        lambda forecast: pytest.fail("Missing schema must be rejected first"),
    )

    with pytest.raises(RuntimeError, match="bash scripts/bootstrap_database.sh"):
        horizon_runner.run_forecast_horizon(current_run_label())


def test_horizon_validation_requires_all_leads_and_all_plr_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        def execute(self, query, parameters):
            return self

        def fetchone(self):
            return (542, 13_008, 24, 0)

    @contextmanager
    def fake_database_connection(**kwargs):
        yield Connection()

    monkeypatch.setattr(horizon_runner, "database_connection", fake_database_connection)

    with pytest.raises(RuntimeError, match="distinct_leads=24"):
        horizon_runner.validate_forecast_horizon(datetime.now(timezone.utc))


def test_horizon_help_explains_sequence_resume_and_utc(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as observed:
        horizon_runner.main(["--help"])

    assert observed.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "lead hours 0 through 24" in output
    assert "partitions are skipped" in output
    assert "UTC, not Berlin-local time" in output
