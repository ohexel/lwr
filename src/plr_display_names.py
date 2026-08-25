"""Install official PLR display names without changing engineering keys."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import requests

from src.database.connection import database_connection
from src.hostrada_snapshot import SnapshotManifest, sha256_file, sorted_plr_ids_sha256


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKBOOK_FILENAME = "lor_2021-01-01_k3_uebersicht_id_namen.xlsx"
WORKBOOK_URL = (
    "https://www.berlin.de/sen/stadt/_assets/stadtdaten/stadtwissen/"
    "lebensweltlich-orientierte-raeume/"
    f"{WORKBOOK_FILENAME}?ts=1780323170"
)
WORKBOOK_SHA256 = (
    "86088afaa3ad8f163849256a037a9d304ff9930ca62ee475d7de37ed5e01dc08"
)
BUNDLED_WORKBOOK = (
    PROJECT_ROOT / "resources" / "static" / "plr_names" / WORKBOOK_FILENAME
)
WORKSHEET_NAME = "LOR_2023_PLR"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _shared_strings(archive: ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(part.text or "" for part in item.iter(f"{{{SPREADSHEET_NS}}}t"))
        for item in root.findall(f"{{{SPREADSHEET_NS}}}si")
    ]


def _worksheet_path(archive: ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheet = next(
        (
            candidate
            for candidate in workbook.iter(f"{{{SPREADSHEET_NS}}}sheet")
            if candidate.get("name") == WORKSHEET_NAME
        ),
        None,
    )
    if sheet is None:
        raise ValueError(f"PLR workbook is missing worksheet {WORKSHEET_NAME}")

    relationship_id = sheet.get(f"{{{DOCUMENT_REL_NS}}}id")
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    relationship = next(
        (
            candidate
            for candidate in relationships.findall(
                f"{{{PACKAGE_REL_NS}}}Relationship"
            )
            if candidate.get("Id") == relationship_id
        ),
        None,
    )
    if relationship is None or not relationship.get("Target"):
        raise ValueError("PLR workbook worksheet relationship is invalid")

    target = PurePosixPath(relationship.get("Target", ""))
    if target.is_absolute() or ".." in target.parts:
        raise ValueError("PLR workbook worksheet path is invalid")
    return str(PurePosixPath("xl") / target)


def _cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(
            part.text or "" for part in cell.iter(f"{{{SPREADSHEET_NS}}}t")
        )

    value = cell.findtext(f"{{{SPREADSHEET_NS}}}v", default="")
    if cell.get("t") == "s":
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError) as exc:
            raise ValueError("PLR workbook contains an invalid shared string") from exc
    return value


def read_plr_display_names(
    workbook_path: Path,
    manifest: SnapshotManifest,
) -> list[tuple[str, str]]:
    """Parse the actual OOXML sheet without adding a spreadsheet dependency."""
    try:
        with ZipFile(workbook_path) as archive:
            shared_strings = _shared_strings(archive)
            worksheet = ElementTree.fromstring(
                archive.read(_worksheet_path(archive))
            )
            parsed_rows = []
            for row in worksheet.iter(f"{{{SPREADSHEET_NS}}}row"):
                cells = {
                    re.sub(r"[0-9]+$", "", cell.get("r", "")): _cell_value(
                        cell,
                        shared_strings,
                    )
                    for cell in row.findall(f"{{{SPREADSHEET_NS}}}c")
                }
                parsed_rows.append((cells.get("A", ""), cells.get("B", "")))
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise ValueError("PLR name directory is not a valid Excel workbook") from exc

    if not parsed_rows or parsed_rows[0] != ("PLR_ID", "PLR_Name"):
        raise ValueError("PLR worksheet must begin with PLR_ID and PLR_Name")

    names = [(plr_id.strip(), name.strip()) for plr_id, name in parsed_rows[1:]]
    if any(not re.fullmatch(r"[0-9]{8}", plr_id) for plr_id, _ in names):
        raise ValueError("Every PLR identifier must contain exactly eight digits")
    if any(not name for _, name in names):
        raise ValueError("Every PLR must have a nonempty display name")
    identifiers = [plr_id for plr_id, _ in names]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("PLR name directory contains duplicate identifiers")
    if len(names) != manifest.plr_count:
        raise ValueError(
            f"Expected {manifest.plr_count} PLR display names; found {len(names)}"
        )
    if sorted_plr_ids_sha256(identifiers) != manifest.sorted_plr_ids_sha256:
        raise ValueError("PLR names do not match the installed reference geography")
    return names


def acquire_plr_name_workbook(
    manifest: SnapshotManifest,
    *,
    project_root: Path = PROJECT_ROOT,
    offline: bool = False,
) -> tuple[Path, str]:
    target = (
        project_root / "data" / "raw" / "berlin" / "lor" / WORKBOOK_FILENAME
    )
    if target.is_file():
        read_plr_display_names(target, manifest)
        return target, "existing"

    target.parent.mkdir(parents=True, exist_ok=True)
    if not offline:
        try:
            LOGGER.info("Acquiring the official 542-PLR display-name workbook")
            response = requests.get(WORKBOOK_URL, timeout=60)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" in content_type:
                raise ValueError("Official PLR workbook URL returned HTML")
            temporary_path = target.with_suffix(".xlsx.part")
            temporary_path.write_bytes(response.content)
            try:
                read_plr_display_names(temporary_path, manifest)
                temporary_path.replace(target)
            finally:
                temporary_path.unlink(missing_ok=True)
            LOGGER.info("Downloaded the official PLR name directory: %s", response.url)
            return target, "official_source"
        except (requests.RequestException, ValueError) as exc:
            LOGGER.warning(
                "Official PLR name download failed; using the verified bundled "
                "workbook: %s",
                exc,
            )

    if sha256_file(BUNDLED_WORKBOOK) != WORKBOOK_SHA256:
        raise RuntimeError("Bundled PLR name workbook failed SHA-256 verification")
    read_plr_display_names(BUNDLED_WORKBOOK, manifest)
    shutil.copyfile(BUNDLED_WORKBOOK, target)
    return target, "bundled_fallback"


def install_plr_display_names(
    manifest: SnapshotManifest,
    *,
    project_root: Path = PROJECT_ROOT,
    offline: bool = False,
) -> dict[str, object]:
    with database_connection(
        application_name="capstone_plr_display_names"
    ) as connection:
        existing = connection.execute(
            """
            SELECT plr_id, plr_name
            FROM analytical.plr_display_name
            WHERE geography_version = %s
            ORDER BY plr_id
            """,
            (manifest.geography_version,),
        ).fetchall()
        if (
            len(existing) == manifest.plr_count
            and sorted_plr_ids_sha256([str(row[0]) for row in existing])
            == manifest.sorted_plr_ids_sha256
            and all(str(row[1]).strip() for row in existing)
        ):
            LOGGER.info(
                "All %s analyst-facing PLR names are already installed",
                len(existing),
            )
            return {"status": "already_installed", "plr_count": len(existing)}

    workbook_path, acquisition_mode = acquire_plr_name_workbook(
        manifest,
        project_root=project_root,
        offline=offline,
    )
    names = read_plr_display_names(workbook_path, manifest)

    with database_connection(
        application_name="capstone_plr_display_names_install"
    ) as connection:
        connection.execute(
            "DELETE FROM analytical.plr_display_name WHERE geography_version = %s",
            (manifest.geography_version,),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO analytical.plr_display_name (
                    plr_id, geography_version, plr_name
                ) VALUES (%s, %s, %s)
                """,
                [
                    (plr_id, manifest.geography_version, name)
                    for plr_id, name in names
                ],
            )

    LOGGER.info("Installed %s analyst-facing PLR display names", len(names))
    return {
        "status": "installed",
        "plr_count": len(names),
        "acquisition_mode": acquisition_mode,
    }
