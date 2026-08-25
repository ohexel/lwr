# Testing strategy

The tests protect operational contracts, not a coverage percentage. Start with
the database-free suite, add real PostgreSQL/PostGIS checks when a database is
available, and reserve the optional historical-rebuild tests for work on that
workflow.

## Run the default suite

```bash
uv run python -m pytest
```

The default selection requires neither a running database nor a live external
source. It exercises forecast identifiers, source acquisition boundaries,
snapshot handling, restart safety, static contracts, and the public forecast
runner. Optional historical-rebuild modules are excluded before collection.

For a focused review of the most important database-free contracts:

```bash
uv run python -m pytest \
  -m "contract and not integration and not acceptance and not historical_rebuild"
```

## Run PostgreSQL/PostGIS integration tests

Initialize the canonical schema first. Then run:

```bash
uv run --env-file .env python -m pytest \
  -m "integration and not acceptance and not historical_rebuild"
```

These tests exercise real database functions, constraints, PostGIS operations,
temperature transformations, population quality gates, and the final serving
contract. They do not assert behavior through a Python imitation of SQL.

## Run operational acceptance tests

The acceptance suite requires completed static initialization, a verified
HOSTRADA reference import, and any forecast partitions expected by the specific
tests:

```bash
uv run --env-file .env python -m pytest \
  -m "acceptance and not historical_rebuild"
```

For the strongest handoff proof, run the suite after materializing one live
forecast in a freshly bootstrapped database. The clean-room acceptance
criterion is described in [../runbook.md](../runbook.md).

## Optional historical-rebuild tests

The ordinary operational installation never downloads or reconstructs the
1995–2025 historical archive. Run these tests only when changing that optional
workflow:

```bash
uv run python -m pytest \
  -m "historical_rebuild and not integration"
```

For the database-backed historical checks:

```bash
uv run --env-file .env python -m pytest \
  -m "historical_rebuild and integration"
```

Some historical integration checks require historical spatial prerequisites or
materialized source partitions. Follow
[historical-rebuild.md](historical-rebuild.md) before interpreting failures in
that optional environment.

## Contracts worth reviewing

| Concern | Representative tests | Protected assumption |
| --- | --- | --- |
| Forecast identity and source publication | `test_forecast_key.py`, `test_run_forecast.py`, `test_weather_source_contract.py` | Forecast runs are UTC, have supported lead times, and fail clearly when unpublished or expired. |
| Geography and forecast-grid compatibility | `test_icon_grid_contract.py`, `test_spatial_bridge_sql.py` | The declared grid, PLR geometry, and area-weighted bridge remain compatible. |
| Population quality | `test_population_sql_quality_gate.py`, `test_bootstrap.py` | Malformed source records are rejected explicitly; the checked-in CSV fallback remains usable. |
| Analyst-facing PLR names | `test_plr_display_names.py`, `test_bootstrap_acceptance.py` | All 542 official labels match the PLR geography; leading zeroes, German characters, duplicate names, and offline fallback remain correct. |
| Temperature calculation | `test_apparent_temperature_sql.py`, `test_weather_contract_sql.py` | The shade apparent-temperature formula and forecast data grain remain stable. |
| Early forecast filtering | `test_weather_early_filtering.py`, `test_weather_mask_state.py` | Full fields remain validated while only ordered, in-range Berlin-mask cells cross into PostgreSQL. |
| HOSTRADA references | `test_hostrada_reference.py`, `test_hostrada_reference_sql.py`, `test_hostrada_snapshot.py` | Berlin-local calendar rules, sample counts, geography fingerprint, and the lean serving contract remain intact. |
| Bootstrap and release artifacts | `test_bootstrap.py`, `test_bootstrap_snapshot_sql.py`, `test_static_snapshot.py`, `test_distribution_manifest.py` | Canonical initialization is non-destructive, restores are verified, and published manifests match runtime assumptions. |
| End-to-end readiness | `test_bootstrap_acceptance.py` | A clean installation serves all 542 PLRs with valid references, 540 accepted populations, and two explicit rejections without historical backfill data. |
| Raw-file lifecycle | `test_weather_raw_retention.py`, `test_run_forecast.py` | Forecast cleanup is deliberate and retained local source files remain reprocessable. |

Markers are assigned centrally in `tests/conftest.py`; their definitions and
the default selection live in `pyproject.toml`.

## Why some tests use `monkeypatch`

`monkeypatch` is a pytest fixture that temporarily replaces an external
boundary for a single test and automatically restores it afterward. Here it is
appropriate for a network request, database connector, subprocess, or Dagster
entry point when the test needs to verify production control flow without
calling the real service.

For example, a bootstrap-order test can replace external operations with tiny
recording functions and assert that the archive is verified before expensive
static processing. That tests an actual operational guarantee. A fabricated
dataframe that merely reproduces an expected answer without calling project
code does not, and should not be retained merely to increase test counts.

Real PostgreSQL/PostGIS behavior belongs in integration tests; external live
services belong in explicit operational checks. Prefer small fakes and clear
contracts over chains of mocks or tests of implementation trivia.
