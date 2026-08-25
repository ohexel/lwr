"""Protect the boundaries of the optional, single-container Dagster overlay."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dagster_overlay_preserves_database_and_shared_state_boundaries() -> None:
    overlay = (PROJECT_ROOT / "docker" / "dagster.yml").read_text()

    assert "POSTGRES_HOST: postgres" in overlay
    assert 'POSTGRES_PORT: "5432"' in overlay
    assert "DAGSTER_HOME: /app/.dagster_home" in overlay
    assert "- ..:/app" in overlay
    assert "condition: service_healthy" in overlay
    assert 'user: "${DAGSTER_UID:-1000}:${DAGSTER_GID:-1000}"' in overlay
    assert '"127.0.0.1:${DAGSTER_PORT:-3000}:3000"' in overlay
    assert "cpus: ${DAGSTER_CPUS:-2.0}" in overlay
    assert "mem_limit: ${DAGSTER_MEMORY_LIMIT:-2g}" in overlay


def test_dagster_image_keeps_locked_dependencies_outside_project_mount() -> None:
    dockerfile = (PROJECT_ROOT / "docker" / "dagster.dockerfile").read_text()

    assert "FROM python:3.11-slim" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/capstone-venv" in dockerfile
    assert 'PATH="/opt/capstone-venv/bin:${PATH}"' in dockerfile
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "uv sync --frozen --no-install-project --no-dev" in dockerfile


def test_dagster_image_build_excludes_large_project_inputs() -> None:
    ignore_file = (
        PROJECT_ROOT / "docker" / "dagster.dockerfile.dockerignore"
    ).read_text()
    patterns = {
        line.strip()
        for line in ignore_file.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "**" in patterns
    assert {"!pyproject.toml", "!uv.lock", "!README.md"} <= patterns
    assert not any(pattern.startswith("!data") for pattern in patterns)
    assert not any(pattern.startswith("!snapshots") for pattern in patterns)
    assert not any(pattern.startswith("!.venv") for pattern in patterns)
