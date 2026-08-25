"""Keep committed release metadata aligned with actual source contracts."""

from __future__ import annotations

import json
from pathlib import Path
import re

from src.download_afs_population import BUNDLED_FALLBACK_SHA256
from src.static_snapshot import STATIC_SOURCE_PATHS


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_static_release_manifest_matches_runtime_sources_and_population_bytes():
    manifest = json.loads(
        (
            PROJECT_ROOT / "snapshots" / "static-inputs.manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["format_version"] == 1
    assert manifest["artifact"]["filename"] == "static-inputs.tar.xz"
    assert manifest["artifact"]["size_bytes"] > 0
    assert len(manifest["artifact"]["sha256"]) == 64
    assert set(manifest["sources"]) == set(STATIC_SOURCE_PATHS)

    for name, expected_path in STATIC_SOURCE_PATHS.items():
        source = manifest["sources"][name]
        assert source["path"] == expected_path.as_posix()
        assert source["size_bytes"] > 0
        assert len(source["sha256"]) == 64

    assert (
        manifest["sources"]["population"]["sha256"]
        == BUNDLED_FALLBACK_SHA256
    )


def test_architecture_decision_index_links_resolve() -> None:
    index = PROJECT_ROOT / "docs" / "adr" / "README.md"
    destinations = re.findall(r"\]\(([^)]+)\)", index.read_text(encoding="utf-8"))

    missing = [
        destination
        for destination in destinations
        if not (index.parent / destination).is_file()
    ]

    assert missing == []


def test_documented_contract_test_files_exist() -> None:
    guide = (PROJECT_ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    filenames = set(re.findall(r"`(test_[a-z0-9_]+\.py)`", guide))

    missing = sorted(
        filename
        for filename in filenames
        if not (PROJECT_ROOT / "tests" / filename).is_file()
    )

    assert missing == []
