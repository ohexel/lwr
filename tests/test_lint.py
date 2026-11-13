"""Run the repository's ruff rules as part of the normal test suite."""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.contract
def test_ruff_rules_pass() -> None:
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed; run `uv sync` to include the dev group")
    result = subprocess.run(
        [ruff, "check", "--no-cache", "."],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
