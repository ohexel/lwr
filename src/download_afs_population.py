#!/usr/bin/env python3
"""
Download the known AfS Berlin LOR population CSV from the current direct
download URL and document provenance so URL/content changes can be detected.

Published AfS resource URL:
    https://www.statistik-berlin-brandenburg.de/opendata/EWR_L21_202512E_Matrix.csv

Current direct download URL:
    https://download.statistik-berlin-brandenburg.de/
    c9771e49e9b212b3/f6a9c1df6163/EWR_L21_202512E_Matrix.csv

Catalogue:
    Berlin Open Data dataset for 2025-12-31 LOR population.

Outputs:
    EWR_L21_202512E_Matrix.csv
    metadata.json
    download.log
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests


CATALOGUE_URL = (
    "https://daten.berlin.de/datensaetze/"
    "einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025"
)

CKAN_API_URL = "https://datenregister.berlin.de/api/3/action/package_show"

DATASET_ID = (
    "einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025"
)

PUBLISHED_RESOURCE_URL = (
    "https://www.statistik-berlin-brandenburg.de/"
    "opendata/EWR_L21_202512E_Matrix.csv"
)

DIRECT_DOWNLOAD_URL = (
    "https://download.statistik-berlin-brandenburg.de/"
    "c9771e49e9b212b3/f6a9c1df6163/"
    "EWR_L21_202512E_Matrix.csv"
)

OUTPUT_FILENAME = "EWR_L21_202512E_Matrix.csv"
REFERENCE_DATE = "2025-12-31"
TIMEOUT_SECONDS = 60


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_logging(output_dir: Path) -> logging.Logger:
    logger = logging.getLogger("afs_population_download")
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


def fetch_catalogue_metadata(session: requests.Session) -> dict:
    response = session.get(
        CKAN_API_URL,
        params={"id": DATASET_ID},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError("Berlin CKAN API returned success=false")

    dataset = payload["result"]

    return {
        "catalogue_url": CATALOGUE_URL,
        "api_endpoint": CKAN_API_URL,
        "api_request_url": response.url,
        "dataset_id": dataset.get("id"),
        "dataset_name": dataset.get("name"),
        "title": dataset.get("title"),
        "description": dataset.get("notes"),
        "publisher": (
            dataset.get("organization", {}).get("title")
            if isinstance(dataset.get("organization"), dict)
            else None
        ),
        "license_id": dataset.get("license_id"),
        "license_title": dataset.get("license_title"),
        "metadata_created": dataset.get("metadata_created"),
        "metadata_modified": dataset.get("metadata_modified"),
    }


def download_csv(session: requests.Session, output_path: Path) -> dict:
    response = session.get(
        DIRECT_DOWNLOAD_URL,
        timeout=TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    response.raise_for_status()

    content = response.content
    if not content:
        raise RuntimeError("Downloaded file is empty")

    prefix = content[:500].lstrip().lower()
    if prefix.startswith(b"<html") or prefix.startswith(b"<!doctype html"):
        raise RuntimeError(
            "Direct download URL returned HTML rather than CSV. "
            f"Resolved URL: {response.url}"
        )

    output_path.write_bytes(content)

    redirect_chain = [
        {
            "status_code": item.status_code,
            "url": item.url,
            "location": item.headers.get("Location"),
        }
        for item in response.history
    ]

    return {
        "published_resource_url": PUBLISHED_RESOURCE_URL,
        "configured_direct_download_url": DIRECT_DOWNLOAD_URL,
        "resolved_download_url": response.url,
        "redirect_chain": redirect_chain,
        "http_status": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "content_disposition": response.headers.get("Content-Disposition"),
        "filename": output_path.name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download AfS Berlin LOR population CSV from the direct URL."
    )
    parser.add_argument(
        "--output-dir",
        default=f"data/raw/population/{REFERENCE_DATE}",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents = True, exist_ok = True)
    logger = configure_logging(output_dir)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "berlin-capstone-afs-downloader/3.0"}
    )

    metadata = {
        "started_at": now_utc(),
        "dataset_reference_date": REFERENCE_DATE,
        "catalogue": {},
        "source_urls": {
            "catalogue_url": CATALOGUE_URL,
            "published_resource_url": PUBLISHED_RESOURCE_URL,
            "configured_direct_download_url": DIRECT_DOWNLOAD_URL,
        },
        "download": {},
    }

    try:
        logger.info("Fetching Berlin Open Data catalogue metadata")
        logger.info("Catalogue URL: %s", CATALOGUE_URL)
        logger.info(
            "Catalogue API call: GET %s?id=%s",
            CKAN_API_URL,
            DATASET_ID,
        )

        catalogue = fetch_catalogue_metadata(session)
        metadata["catalogue"] = catalogue

        logger.info("Catalogue title: %s", catalogue.get("title"))
        logger.info("Publisher: %s", catalogue.get("publisher"))
        logger.info("License: %s", catalogue.get("license_title"))

        logger.info("Published AfS resource URL: %s", PUBLISHED_RESOURCE_URL)
        logger.info("Configured direct download URL: %s", DIRECT_DOWNLOAD_URL)

        output_path = output_dir / OUTPUT_FILENAME
        download_info = download_csv(session, output_path)
        metadata["download"] = download_info

        if download_info["resolved_download_url"] != DIRECT_DOWNLOAD_URL:
            logger.warning(
                "Direct download URL resolved elsewhere: %s",
                download_info["resolved_download_url"],
            )

        if download_info["redirect_chain"]:
            for redirect in download_info["redirect_chain"]:
                logger.info(
                    "Redirect: %s -> %s",
                    redirect["url"],
                    redirect["location"],
                )
        else:
            logger.info("No HTTP redirect from configured direct download URL")

        logger.info(
            "Final resolved download URL: %s",
            download_info["resolved_download_url"],
        )
        logger.info("Saved file: %s", output_path)
        logger.info("Bytes: %d", download_info["size_bytes"])
        logger.info("SHA-256: %s", download_info["sha256"])

        metadata["status"] = "success"

    except Exception as exc:
        logger.exception("Acquisition failed: %s", exc)
        metadata["status"] = "failed"
        metadata["error"] = str(exc)

    metadata["completed_at"] = now_utc()

    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding = "utf-8",
    )
    logger.info("Metadata written to %s", metadata_path)

    return 0 if metadata["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
