"""Acquire Germany-layer static sources: BKG VG250 and Zensus 2022.

Usage:
    uv run --env-file .env python -m src.germany.acquire [--only vg250|zensus]

VG250 is a public download. Zensus 2022 tables require a personal API token
(free registration at https://ergebnisse.zensus2022.de/), read from the
environment variable ZENSUS2022_TOKEN. The token is sent only as an HTTP
header and is never logged, written to metadata, or included in errors.

The Zensus API echoes the token in the "Username" field of JSON responses,
so JSON bodies are redacted before they are ever shown.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.germany.contract import (
    VG250_CONTRACT,
    ZENSUS_CONTRACT,
    ZensusContract,
    ZensusExtract,
)


LOGGER = logging.getLogger("germany_acquire")
TIMEOUT_SECONDS = 300
REDACTED_KEYS = frozenset({"Username", "username", "Parameter", "password"})


@dataclass(frozen=True)
class AcquiredFile:
    path: Path
    sha256: str
    size_bytes: int
    downloaded: bool


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_api_payload(payload: object) -> object:
    """Remove credential-bearing fields from a Zensus/GENESIS JSON payload."""
    if isinstance(payload, dict):
        return {
            key: redact_api_payload(value)
            for key, value in payload.items()
            if key not in REDACTED_KEYS
        }
    if isinstance(payload, list):
        return [redact_api_payload(item) for item in payload]
    return payload


def _describe_non_file_response(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (
            f"HTTP {response.status_code}, "
            f"Content-Type {response.headers.get('Content-Type')!r}"
        )
    return json.dumps(redact_api_payload(payload), ensure_ascii=False)[:2000]


def _write_metadata(path: Path, metadata: dict) -> None:
    path.with_suffix(path.suffix + ".acquisition.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# VG250
# ---------------------------------------------------------------------------
def acquire_vg250(
    session: requests.Session,
    project_root: Path = Path("."),
) -> AcquiredFile:
    contract = VG250_CONTRACT
    target = project_root / contract.archive_path

    if target.exists():
        existing = sha256_file(target)
        if existing == contract.archive_sha256:
            LOGGER.info("VG250 archive already present and verified: %s", target)
            return AcquiredFile(target, existing, target.stat().st_size, False)
        raise RuntimeError(
            f"Existing VG250 archive {target} has SHA-256 {existing}, "
            f"expected {contract.archive_sha256}. Refusing to overwrite it."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0

    LOGGER.info("Downloading VG250 %s from %s", contract.vintage, contract.source_url)
    with session.get(contract.source_url, stream=True, timeout=TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)

    actual = digest.hexdigest()
    if actual != contract.archive_sha256 or size != contract.archive_size_bytes:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            "VG250 download does not match its contract: "
            f"sha256={actual} size={size}, expected "
            f"sha256={contract.archive_sha256} size={contract.archive_size_bytes}"
        )

    partial.replace(target)
    _write_metadata(
        target,
        {
            "source_url": contract.source_url,
            "vintage": contract.vintage.isoformat(),
            "sha256": actual,
            "size_bytes": size,
            "retrieved_at_utc": _utc_now(),
            "license": contract.license,
        },
    )
    return AcquiredFile(target, actual, size, True)


def extract_vg250_geopackage(
    archive_path: Path,
    project_root: Path = Path("."),
) -> Path:
    """Extract the GeoPackage next to the verified archive (idempotent)."""
    contract = VG250_CONTRACT
    target = archive_path.parent / Path(contract.geopackage_member).name
    if target.exists():
        return target

    with zipfile.ZipFile(archive_path) as archive:
        member = archive.getinfo(contract.geopackage_member)
        partial = target.with_suffix(".gpkg.part")
        with archive.open(member) as source, partial.open("wb") as sink:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                sink.write(chunk)
    partial.replace(target)
    return target


# ---------------------------------------------------------------------------
# Zensus 2022
# ---------------------------------------------------------------------------
def zensus_token(contract: ZensusContract = ZENSUS_CONTRACT) -> str:
    token = os.environ.get(contract.token_environment_variable, "").strip()
    if not token:
        raise RuntimeError(
            f"{contract.token_environment_variable} is not set. Register at "
            "https://ergebnisse.zensus2022.de/ and copy the personal API "
            "token from the 'Webservice-Schnittstelle (API)' dialog."
        )
    return token


def extract_single_csv(archive_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(
                f"Expected exactly one CSV in the Zensus response, found {members}"
            )
        return archive.read(members[0])


def download_zensus_extract(
    session: requests.Session,
    extract: ZensusExtract,
    token: str,
    contract: ZensusContract = ZENSUS_CONTRACT,
) -> bytes:
    response = session.post(
        contract.tablefile_url,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "username": token,
        },
        data={
            "name": contract.table_code,
            "regionalvariable": extract.regional_variable,
            "format": "ffcsv",
            "compress": "false",
            "language": "de",
        },
        timeout=TIMEOUT_SECONDS,
    )
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or "zip" not in content_type:
        raise RuntimeError(
            f"Zensus API did not return a table file for "
            f"{contract.table_code}/{extract.regional_variable}: "
            f"{_describe_non_file_response(response)}"
        )
    return extract_single_csv(response.content)


def acquire_zensus(
    session: requests.Session,
    project_root: Path = Path("."),
    contract: ZensusContract = ZENSUS_CONTRACT,
) -> list[AcquiredFile]:
    results: list[AcquiredFile] = []
    token: str | None = None

    for extract in contract.extracts:
        target = project_root / extract.csv_path(contract.table_code)

        if target.exists():
            existing = sha256_file(target)
            if not extract.csv_sha256 or existing == extract.csv_sha256:
                LOGGER.info("Zensus extract already present: %s", target)
                results.append(
                    AcquiredFile(target, existing, target.stat().st_size, False)
                )
                continue
            raise RuntimeError(
                f"Existing Zensus extract {target} has SHA-256 {existing}, "
                f"expected {extract.csv_sha256}. Refusing to overwrite it."
            )

        token = token or zensus_token(contract)
        LOGGER.info(
            "Downloading Zensus %s at %s",
            contract.table_code,
            extract.regional_variable,
        )
        csv_bytes = download_zensus_extract(session, extract, token, contract)
        actual = sha256_bytes(csv_bytes)
        if extract.csv_sha256 and actual != extract.csv_sha256:
            raise RuntimeError(
                f"Zensus extract {extract.regional_variable} has SHA-256 "
                f"{actual}, expected {extract.csv_sha256}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".csv.part")
        partial.write_bytes(csv_bytes)
        partial.replace(target)
        _write_metadata(
            target,
            {
                "source_url": contract.tablefile_url,
                "table_code": contract.table_code,
                "regional_variable": extract.regional_variable,
                "format": "ffcsv",
                "sha256": actual,
                "size_bytes": len(csv_bytes),
                "retrieved_at_utc": _utc_now(),
                "license": contract.license,
            },
        )
        results.append(AcquiredFile(target, actual, len(csv_bytes), True))

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=("vg250", "zensus"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    summary: dict[str, object] = {}
    with requests.Session() as session:
        if args.only in (None, "vg250"):
            archive = acquire_vg250(session)
            gpkg = extract_vg250_geopackage(archive.path)
            summary["vg250"] = {
                "archive": str(archive.path),
                "sha256": archive.sha256,
                "geopackage": str(gpkg),
            }
        if args.only in (None, "zensus"):
            summary["zensus"] = [
                {"path": str(item.path), "sha256": item.sha256, "bytes": item.size_bytes}
                for item in acquire_zensus(session)
            ]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
