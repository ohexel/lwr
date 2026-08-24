"""Create and safely restore an optional offline static-source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import tarfile
import tempfile
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_SOURCE_PATHS = {
    "lor_plr": Path("data/raw/berlin/lor/lor_planungsraum.geojson"),
    "population": Path(
        "data/raw/population/2025-12-31/EWR_L21_202512E_Matrix.csv"
    ),
    "icon_grid": Path("data/raw/icon_d2_grid/icon_grid_0047_R19B07_L.nc.bz2"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_static_snapshot(
    output_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}

    for source_name, relative_path in STATIC_SOURCE_PATHS.items():
        source_path = project_root / relative_path
        if not source_path.is_file():
            raise FileNotFoundError(
                f"Cannot build static snapshot; missing {source_name}: {source_path}"
            )
        entries[source_name] = {
            "path": relative_path.as_posix(),
            "size_bytes": source_path.stat().st_size,
            "sha256": sha256_file(source_path),
        }

    manifest = {"format_version": 1, "sources": entries}
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.partial")

    try:
        with tarfile.open(temporary_path, mode="w:xz") as archive:
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_bytes)
            manifest_info.mode = 0o644
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))

            for relative_path in STATIC_SOURCE_PATHS.values():
                archive.add(
                    project_root / relative_path,
                    arcname=relative_path.as_posix(),
                    recursive=False,
                )

        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return {
        "archive_path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "sources": entries,
    }


def read_static_manifest(snapshot_path: Path) -> dict[str, Any]:
    with tarfile.open(snapshot_path, mode="r:*") as archive:
        try:
            manifest_member = archive.getmember("manifest.json")
        except KeyError as exc:
            raise ValueError("Static snapshot does not contain manifest.json") from exc

        if not manifest_member.isfile():
            raise ValueError("Static snapshot manifest must be a regular file")

        content = archive.extractfile(manifest_member)
        if content is None:
            raise ValueError("Static snapshot manifest could not be read")
        manifest = json.load(content)

    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported static snapshot manifest version")

    for source_name, relative_path in STATIC_SOURCE_PATHS.items():
        entry = manifest.get("sources", {}).get(source_name)
        if not isinstance(entry, dict):
            raise ValueError(f"Static snapshot does not include {source_name}")
        if entry.get("path") != relative_path.as_posix():
            raise ValueError(f"Unexpected archive path for {source_name}")

    return manifest


def restore_static_source(
    snapshot_path: Path,
    source_name: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    if source_name not in STATIC_SOURCE_PATHS:
        raise ValueError(f"Unknown static source: {source_name}")

    manifest = read_static_manifest(snapshot_path)
    entry = manifest["sources"][source_name]
    relative_path = STATIC_SOURCE_PATHS[source_name]
    target_path = project_root / relative_path

    if target_path.exists():
        if (
            target_path.is_file()
            and target_path.stat().st_size == int(entry["size_bytes"])
            and sha256_file(target_path) == entry["sha256"]
        ):
            LOGGER.info("Static source already matches the snapshot: %s", target_path)
            return target_path
        raise FileExistsError(
            f"Refusing to overwrite an existing static source: {target_path}"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tarfile.open(snapshot_path, mode="r:*") as archive:
            try:
                member = archive.getmember(relative_path.as_posix())
            except KeyError as exc:
                raise ValueError(
                    f"Static snapshot is missing {relative_path.as_posix()}"
                ) from exc

            if not member.isfile():
                raise ValueError(f"Static snapshot entry is not a regular file: {member.name}")
            if member.size != int(entry["size_bytes"]):
                raise ValueError(f"Static snapshot size mismatch: {member.name}")

            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Static snapshot entry could not be read: {member.name}")

            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target_path.parent,
                prefix=f".{target_path.name}.",
                suffix=".partial",
                delete=False,
            ) as destination:
                temporary_path = Path(destination.name)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(chunk)
                    digest.update(chunk)

            if digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"Static snapshot checksum mismatch: {member.name}")

            temporary_path.replace(target_path)
            LOGGER.info("Restored %s from static snapshot", target_path)
            return target_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or restore an optional static-source fallback snapshot."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create_parser = commands.add_parser("create")
    create_parser.add_argument("--output", type=Path, required=True)

    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--archive", type=Path, required=True)

    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--archive", type=Path, required=True)
    restore_parser.add_argument("--source", choices=STATIC_SOURCE_PATHS)

    arguments = parser.parse_args(argv)

    if arguments.command == "create":
        result = create_static_snapshot(arguments.output)
    elif arguments.command == "inspect":
        result = read_static_manifest(arguments.archive)
    else:
        source_names = (
            [arguments.source] if arguments.source else list(STATIC_SOURCE_PATHS)
        )
        result = {
            source_name: str(
                restore_static_source(arguments.archive, source_name)
            )
            for source_name in source_names
        }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
