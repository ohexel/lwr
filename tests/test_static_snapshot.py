import hashlib
import io
import json
import tarfile

import pytest

from src.static_snapshot import (
    STATIC_SOURCE_PATHS,
    create_static_snapshot,
    read_static_manifest,
    restore_static_source,
)


def _write_sources(project_root):
    for source_name, relative_path in STATIC_SOURCE_PATHS.items():
        source_path = project_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(f"fixture-{source_name}".encode("utf-8"))


def test_static_snapshot_contains_all_required_source_files(tmp_path):
    project_root = tmp_path / "source"
    _write_sources(project_root)
    archive_path = tmp_path / "static-inputs.tar.xz"

    result = create_static_snapshot(archive_path, project_root=project_root)
    manifest = read_static_manifest(archive_path)

    assert set(result["sources"]) == {"lor_plr", "population", "icon_grid"}
    assert set(manifest["sources"]) == set(STATIC_SOURCE_PATHS)


def test_static_snapshot_restores_one_verified_source(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _write_sources(source_root)
    archive_path = tmp_path / "static-inputs.tar.xz"
    create_static_snapshot(archive_path, project_root=source_root)

    target = restore_static_source(
        archive_path,
        "lor_plr",
        project_root=target_root,
    )

    assert target.read_bytes() == b"fixture-lor_plr"
    assert not (target_root / STATIC_SOURCE_PATHS["icon_grid"]).exists()


def test_static_snapshot_refuses_to_overwrite_an_unrelated_source(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    _write_sources(source_root)
    archive_path = tmp_path / "static-inputs.tar.xz"
    create_static_snapshot(archive_path, project_root=source_root)
    target = target_root / STATIC_SOURCE_PATHS["population"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"preserve-me")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        restore_static_source(
            archive_path,
            "population",
            project_root=target_root,
        )

    assert target.read_bytes() == b"preserve-me"


def test_static_snapshot_rejects_an_unexpected_manifest_path(tmp_path):
    source_root = tmp_path / "source"
    _write_sources(source_root)
    entries = {
        name: {
            "path": relative_path.as_posix(),
            "size_bytes": len(f"fixture-{name}"),
            "sha256": hashlib.sha256(
                f"fixture-{name}".encode("utf-8")
            ).hexdigest(),
        }
        for name, relative_path in STATIC_SOURCE_PATHS.items()
    }
    entries["population"]["path"] = "../../outside.csv"
    archive_path = tmp_path / "unsafe.tar.xz"
    content = json.dumps(
        {"format_version": 1, "sources": entries}
    ).encode("utf-8")

    with tarfile.open(archive_path, mode="w:xz") as archive:
        member = tarfile.TarInfo("manifest.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    with pytest.raises(ValueError, match="Unexpected archive path"):
        read_static_manifest(archive_path)
