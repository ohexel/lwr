import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import bootstrap as operational_bootstrap
from src.bootstrap import StaticState
from src.dagster_pipeline.static_bootstrap_job import OPERATIONAL_STATIC_JOB
from src import download_afs_population
from src.download_afs_population import (
    BUNDLED_FALLBACK_PATH,
    BUNDLED_FALLBACK_SHA256,
    restore_bundled_csv,
)
from src.hostrada_snapshot import sha256_file
from src.hostrada_snapshot import SnapshotManifest
from src.icon_grid_contract import ICON_D2_GRID_CONTRACT
from src.run_forecast import run_forecast


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_operational_static_job_is_independent_of_historical_reconstruction():
    assert OPERATIONAL_STATIC_JOB.name == "operational_static_bootstrap"


def test_operational_static_state_requires_every_static_dependency():
    complete = StaticState(
        plr_count=542,
        raw_population_count=542,
        accepted_population_count=541,
        rejected_population_count=1,
        icon_cell_count=ICON_D2_GRID_CONTRACT.cell_count,
        bridge_row_count=1000,
        mask_cell_count=100,
    )
    incomplete = StaticState(
        plr_count=542,
        raw_population_count=542,
        accepted_population_count=541,
        rejected_population_count=1,
        icon_cell_count=ICON_D2_GRID_CONTRACT.cell_count,
        bridge_row_count=1000,
        mask_cell_count=0,
    )

    assert complete.is_complete(542)
    assert not incomplete.is_complete(542)


def test_bundled_population_fallback_preserves_verified_source_bytes(tmp_path):
    output_path = tmp_path / "population.csv"

    result = restore_bundled_csv(output_path, BUNDLED_FALLBACK_PATH)

    assert result["acquisition_mode"] == "bundled_fallback"
    assert result["sha256"] == BUNDLED_FALLBACK_SHA256
    assert sha256_file(output_path) == BUNDLED_FALLBACK_SHA256


def test_population_download_survives_catalogue_and_direct_source_failures(
    tmp_path,
    monkeypatch,
):
    class UnavailableSession:
        def __init__(self):
            self.headers = {}

        def get(self, *args, **kwargs):
            raise download_afs_population.requests.ConnectionError(
                "fixture source unavailable"
            )

    monkeypatch.setattr(
        download_afs_population.requests,
        "Session",
        UnavailableSession,
    )

    assert download_afs_population.main(
        ["--output-dir", str(tmp_path)]
    ) == 0

    metadata = json.loads(
        (tmp_path / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["catalogue"]["status"] == "unavailable"
    assert metadata["download"]["acquisition_mode"] == "bundled_fallback"
    assert metadata["download"]["sha256"] == BUNDLED_FALLBACK_SHA256


def test_canonical_bootstrap_never_replays_numbered_development_migrations():
    bootstrap_script = (
        PROJECT_ROOT / "scripts" / "bootstrap_database.sh"
    ).read_text(encoding="utf-8")

    assert "sql/bootstrap_schema.sql" in bootstrap_script
    assert "sql/[0-9][0-9][0-9]_*.sql" not in bootstrap_script
    assert "TRUNCATE TABLE" not in bootstrap_script


def test_canonical_schema_excludes_unneeded_postgis_extensions():
    schema = (PROJECT_ROOT / "sql" / "bootstrap_schema.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;" in schema
    assert "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch" not in schema
    assert "CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder" not in schema
    assert "CREATE EXTENSION IF NOT EXISTS postgis_topology" not in schema
    assert "analytical.check_hostrada_reference_snapshot(" in schema


def test_manual_forecast_rejects_a_lead_time_outside_the_project_contract():
    with pytest.raises(ValueError, match="Unsupported project lead time"):
        run_forecast("20260824T1200", "PT003H00M")


def test_operational_bootstrap_verifies_reference_before_expensive_work(
    tmp_path,
    monkeypatch,
):
    manifest = SnapshotManifest.load()
    complete_state = StaticState(
        plr_count=542,
        raw_population_count=542,
        accepted_population_count=541,
        rejected_population_count=1,
        icon_cell_count=ICON_D2_GRID_CONTRACT.cell_count,
        bridge_row_count=1000,
        mask_cell_count=100,
    )
    events = []
    monkeypatch.setattr(
        operational_bootstrap,
        "DatabaseSettings",
        SimpleNamespace(from_env=lambda: None),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "SnapshotManifest",
        SimpleNamespace(load=lambda path: manifest),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "verify_archive",
        lambda archive_path, observed_manifest: events.append("verify"),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "initialize_database",
        lambda project_root: events.append("database"),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "materialize_static_inputs",
        lambda observed_manifest, **kwargs: (
            events.append("static") or complete_state
        ),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "restore_archive",
        lambda archive_path, observed_manifest, **kwargs: (
            events.append("restore") or {"status": "imported"}
        ),
    )
    monkeypatch.setattr(
        operational_bootstrap,
        "ensure_dagster_home",
        lambda project_root: tmp_path / ".dagster_home",
    )

    result = operational_bootstrap.bootstrap(
        tmp_path / "reference.pgcustom",
        project_root=tmp_path,
    )

    assert events == ["verify", "database", "static", "restore"]
    assert result["status"] == "ready"
    assert result["weather_sensor_default_status"] == "STOPPED"
