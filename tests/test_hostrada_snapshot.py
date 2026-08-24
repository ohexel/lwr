import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src import hostrada_snapshot
from src.hostrada_snapshot import (
    DEFAULT_MANIFEST_PATH,
    SnapshotManifest,
    SnapshotQuality,
    sorted_plr_ids_sha256,
    verify_archive,
)


def test_hostrada_snapshot_manifest_preserves_the_operational_contract():
    manifest = SnapshotManifest.load()

    assert manifest.archive_filename == "hostrada-reference-1995-2025.pgcustom"
    assert manifest.archive_size_bytes == 231645982
    assert manifest.archive_sha256 == (
        "a4552e534c59a44529849c010b5771598fc41b3cd3ae1023d03f07ec79825145"
    )
    assert manifest.geography_version == "2023-01-01"
    assert manifest.plr_count == 542
    assert manifest.expected_plr_rows == 4_747_920
    assert manifest.expected_berlin_rows == 8_760
    assert manifest.expected_observation_count == 271_559


def test_sorted_plr_fingerprint_is_order_independent():
    expected = hashlib.sha256(b"01000001\n01000002\n").hexdigest()

    assert sorted_plr_ids_sha256(["01000002", "01000001"]) == expected


def test_hostrada_archive_verification_checks_size_before_checksum(tmp_path):
    archive = tmp_path / "reference.pgcustom"
    archive.write_bytes(b"fixture")
    manifest = replace(
        SnapshotManifest.load(),
        archive_size_bytes=8,
    )

    with pytest.raises(ValueError, match="size does not match"):
        verify_archive(archive, manifest)


def test_hostrada_archive_verification_rejects_wrong_checksum(tmp_path):
    archive = tmp_path / "reference.pgcustom"
    archive.write_bytes(b"fixture")
    manifest = replace(
        SnapshotManifest.load(),
        archive_size_bytes=7,
    )

    with pytest.raises(ValueError, match="SHA-256 does not match"):
        verify_archive(archive, manifest)


def test_hostrada_archive_verification_accepts_exact_bytes(tmp_path):
    archive = tmp_path / "reference.pgcustom"
    archive.write_bytes(b"fixture")
    manifest = replace(
        SnapshotManifest.load(),
        archive_size_bytes=7,
        archive_sha256=hashlib.sha256(b"fixture").hexdigest(),
    )

    result = verify_archive(archive, manifest)

    assert result["size_bytes"] == 7
    assert result["sha256"] == hashlib.sha256(b"fixture").hexdigest()


def test_hostrada_snapshot_manifest_rejects_inconsistent_row_counts(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    payload = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["tables"]["analytical.hostrada_plr_hourly_reference"][
        "expected_row_count"
    ] -= 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="row counts are inconsistent"):
        SnapshotManifest.load(manifest_path)


def test_hostrada_snapshot_quality_retains_actionable_counts():
    result = SnapshotQuality.from_row(
        (True, 542, 542, 8760, 271559, 4747920, 8760, 0, 0, 0, 0, 0)
    )

    assert result.passed is True
    assert result.as_dict()["plr_reference_count"] == 4_747_920
    assert result.as_dict()["expected_observation_count"] == 271_559


def test_completed_reference_snapshot_is_not_restored_again(
    tmp_path,
    monkeypatch,
):
    manifest = SnapshotManifest.load()
    archive = tmp_path / "reference.pgcustom"
    archive.write_bytes(b"fixture")
    quality = SnapshotQuality.from_row(
        (True, 542, 542, 8760, 271559, 4747920, 8760, 0, 0, 0, 0, 0)
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "verify_archive",
        lambda archive_path, observed_manifest: {"sha256": "fixture"},
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "verify_installed_geography",
        lambda observed_manifest: {"plr_count": 542},
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "installed_reference_counts",
        lambda: (4_747_920, 8_760),
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "validate_snapshot",
        lambda observed_manifest: quality,
    )

    result = hostrada_snapshot.restore_archive(archive, manifest)

    assert result["status"] == "already_installed"
    assert result["quality"]["plr_reference_count"] == 4_747_920


def test_reference_restore_uses_one_transaction_and_only_reference_tables(
    tmp_path,
    monkeypatch,
):
    manifest = SnapshotManifest.load()
    archive = tmp_path / "reference.pgcustom"
    archive.write_bytes(b"fixture")
    quality = SnapshotQuality.from_row(
        (True, 542, 542, 8760, 271559, 4747920, 8760, 0, 0, 0, 0, 0)
    )
    observed_commands = []
    monkeypatch.setattr(
        hostrada_snapshot,
        "verify_archive",
        lambda archive_path, observed_manifest: {"sha256": "fixture"},
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "verify_installed_geography",
        lambda observed_manifest: {"plr_count": 542},
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "installed_reference_counts",
        lambda: (0, 0),
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "validate_snapshot",
        lambda observed_manifest: quality,
    )
    monkeypatch.setattr(
        hostrada_snapshot,
        "DatabaseSettings",
        SimpleNamespace(
            from_env=lambda: SimpleNamespace(user="capstone", database="capstone")
        ),
    )
    monkeypatch.setattr(
        hostrada_snapshot.subprocess,
        "run",
        lambda command, **kwargs: observed_commands.append((command, kwargs)),
    )

    result = hostrada_snapshot.restore_archive(
        archive,
        manifest,
        project_root=tmp_path,
    )

    assert result["status"] == "imported"
    assert len(observed_commands) == 1
    command, arguments = observed_commands[0]
    assert "--single-transaction" in command
    assert "--exit-on-error" in command
    assert "--table=hostrada_plr_hourly_reference" in command
    assert "--table=hostrada_berlin_hourly_reference" in command
    assert arguments["cwd"] == tmp_path
