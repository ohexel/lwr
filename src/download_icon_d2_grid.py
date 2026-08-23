#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.icon_grid_contract import (
    ICON_D2_GRID_CONTRACT,
)


GRID_URL = ICON_D2_GRID_CONTRACT.source_url
GRID_FILENAME = ICON_D2_GRID_CONTRACT.source_path.name
DEFAULT_OUTPUT_DIR = str(
    ICON_D2_GRID_CONTRACT.source_path.parent
)
TIMEOUT_SECONDS = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_logging(output_dir: Path) -> logging.Logger:
    logger = logging.getLogger("download_icon_d2_grid")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = logging.FileHandler(
        output_dir / "download.log",
        mode="w",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(session: requests.Session, target: Path, logger: logging.Logger) -> dict:
    logger.info("Downloading ICON-D2 grid definition")
    logger.info("Source URL: %s", GRID_URL)

    with session.get(
        GRID_URL,
        stream=True,
        timeout=TIMEOUT_SECONDS,
        allow_redirects=True,
    ) as response:
        response.raise_for_status()

        logger.info("HTTP status: %s", response.status_code)
        logger.info("Resolved URL: %s", response.url)
        logger.info("Content-Type: %s", response.headers.get("Content-Type"))
        logger.info("Content-Length: %s", response.headers.get("Content-Length"))

        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

        redirect_chain = [
            {
                "status_code": item.status_code,
                "url": item.url,
                "location": item.headers.get("Location"),
            }
            for item in response.history
        ]

        return {
            "source_url": GRID_URL,
            "resolved_url": response.url,
            "redirect_chain": redirect_chain,
            "http_status": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "content_length_header": response.headers.get("Content-Length"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the official DWD ICON-D2 grid definition."
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Landing directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing grid file. Default is to fail if it already exists.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = configure_logging(output_dir)

    target = output_dir / GRID_FILENAME
    metadata_path = output_dir / "metadata.json"

    metadata = {
        "started_at": utc_now(),
        "dataset": "DWD ICON-D2 grid definition",
        "grid_id": ICON_D2_GRID_CONTRACT.dwd_grid_number,
        "grid_name": ICON_D2_GRID_CONTRACT.dwd_grid_name,
        "source": {
            "publisher": "Deutscher Wetterdienst (DWD)",
            "source_url": GRID_URL,
            "access": "DWD Open Data",
            "authentication_required": False,
        },
        "download": {},
    }

    try:
        if target.exists() and not args.overwrite:
            raise FileExistsError(
                f"{target} already exists. "
                "Use --overwrite if you intentionally want to replace it."
            )

        session = requests.Session()
        session.headers.update(
            {"User-Agent": "berlin-capstone-icon-d2-grid-downloader/1.0"}
        )

        download_info = download_file(session, target, logger)

        size_bytes = target.stat().st_size
        checksum = sha256_file(target)

        metadata["download"] = {
            **download_info,
            "filename": target.name,
            "size_bytes": size_bytes,
            "sha256": checksum,
        }
        metadata["status"] = "success"

        logger.info("Saved file: %s", target)
        logger.info("Size bytes: %s", size_bytes)
        logger.info("SHA-256: %s", checksum)

    except Exception as exc:
        logger.exception("Download failed: %s", exc)
        metadata["status"] = "failed"
        metadata["error"] = str(exc)

        if target.exists() and metadata["status"] == "failed":
            target.unlink(missing_ok=True)

    metadata["completed_at"] = utc_now()

    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Metadata written to %s", metadata_path)

    return 0 if metadata["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
