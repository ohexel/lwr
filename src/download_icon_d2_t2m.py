#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests


RUN_INDEX_URL = (
    "https://opendata.dwd.de/weather/nwp/v1/m/"
    "icon-d2-ruc/p/T_2M/r/"
)

LEAD0_RELATIVE_PATH = "s/PT000H00M.grib2"

DEFAULT_OUTPUT_DIR = "data/raw/icon-d2-t2m"
TIMEOUT_SECONDS = 120

# Run timestamps are extracted after URL-decoding the directory HTML.
RUN_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_console_logger() -> logging.Logger:
    logger = logging.getLogger("download_icon_d2_t2m_lead0")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def add_file_logging(logger: logging.Logger, log_path: Path) -> None:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_download_url(run_timestamp: str) -> str:
    # urljoin/requests will handle the colon correctly in the path.
    run_url = urljoin(RUN_INDEX_URL, f"{run_timestamp}/")
    return urljoin(run_url, LEAD0_RELATIVE_PATH)


def lead0_exists(
    session: requests.Session,
    run_timestamp: str,
    logger: logging.Logger,
) -> bool:
    url = build_download_url(run_timestamp)

    try:
        response = session.head(
            url,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=True,
        )

        # Some HTTP servers do not implement HEAD consistently.
        if response.status_code == 405:
            response = session.get(
                url,
                stream=True,
                timeout=TIMEOUT_SECONDS,
                allow_redirects=True,
            )

        if response.ok:
            logger.info(
                "Lead-0 file available for run %s UTC",
                run_timestamp,
            )
            return True

        logger.info(
            "Lead-0 unavailable for run %s UTC (HTTP %s)",
            run_timestamp,
            response.status_code,
        )
        return False

    except requests.RequestException as exc:
        logger.warning(
            "Could not check lead-0 for run %s: %s",
            run_timestamp,
            exc,
        )
        return False


def discover_latest_available_run(
    session: requests.Session,
    logger: logging.Logger,
) -> str:
    logger.info("Checking DWD ICON-D2-RUC T_2M run index")
    logger.info("Run index URL: %s", RUN_INDEX_URL)

    response = session.get(
        RUN_INDEX_URL,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    # DWD encodes ":" in href targets as "%3A".
    # Decoding first means discovery does not depend on the exact HTML markup.
    decoded_html = unquote(response.text)

    timestamps = sorted(
        set(RUN_PATTERN.findall(decoded_html)),
        key=lambda value: datetime.strptime(value, "%Y-%m-%dT%H:%M"),
        reverse=True,
    )

    if not timestamps:
        preview = decoded_html[:500].replace("\n", " ")
        raise RuntimeError(
            "No ICON-D2-RUC T_2M run timestamps found in DWD index. "
            f"Response preview: {preview!r}"
        )

    logger.info(
        "Found %d advertised T_2M run directories",
        len(timestamps),
    )
    logger.info(
        "Newest advertised run: %s UTC",
        timestamps[0],
    )

    # A run directory can appear shortly before every file is ready.
    # Check newest first, then fall back to recent runs.
    for run_timestamp in timestamps[:6]:
        if lead0_exists(session, run_timestamp, logger):
            return run_timestamp

    raise RuntimeError(
        "Found recent ICON-D2-RUC T_2M run directories, but no "
        "PT000H00M.grib2 file was available in the six newest runs."
    )


def validate_run_timestamp(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise ValueError(
            "--run must have format YYYY-MM-DDTHH:MM, "
            "for example 2026-08-13T14:00"
        ) from exc

    return parsed.strftime("%Y-%m-%dT%H:%M")


def download_grib(
    session: requests.Session,
    download_url: str,
    target: Path,
    logger: logging.Logger,
) -> dict:
    logger.info("Downloading T_2M lead-0 GRIB2")
    logger.info("Download URL: %s", download_url)

    with session.get(
        download_url,
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

        return {
            "requested_url": download_url,
            "resolved_url": response.url,
            "redirect_chain": [
                {
                    "status_code": item.status_code,
                    "url": item.url,
                    "location": item.headers.get("Location"),
                }
                for item in response.history
            ],
            "http_status": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "content_length_header": response.headers.get("Content-Length"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download the latest available DWD ICON-D2-RUC "
            "T_2M lead-0 GRIB2 field."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Landing directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--run",
        help=(
            "Optional UTC run timestamp, e.g. 2026-08-13T14:00. "
            "If omitted, discover the latest run with an available "
            "PT000H00M.grib2 file."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing raw file for the selected run.",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    logger = make_console_logger()

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "berlin-capstone-icon-d2-t2m-downloader/2.0"}
    )

    run_timestamp = (
        validate_run_timestamp(args.run)
        if args.run
        else discover_latest_available_run(session, logger)
    )

    run_dir_name = run_timestamp.replace(":", "")
    run_dir = output_root / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)

    add_file_logging(logger, run_dir / "download.log")

    filename = (
        f"icon-d2-ruc_T_2M_{run_dir_name}_PT000H00M.grib2"
    )
    target = run_dir / filename
    metadata_path = run_dir / "metadata.json"

    metadata = {
        "started_at": utc_now(),
        "dataset": "DWD ICON-D2-RUC T_2M",
        "model": "ICON-D2-RUC",
        "parameter": "T_2M",
        "run_timestamp_utc": run_timestamp,
        "forecast_step": "PT000H00M",
        "valid_time_utc": run_timestamp,
        "grid_id": "0047",
        "source": {
            "publisher": "Deutscher Wetterdienst (DWD)",
            "run_index_url": RUN_INDEX_URL,
            "access": "DWD Open Data",
            "authentication_required": False,
        },
        "download": {},
    }

    try:
        logger.info("Selected run: %s UTC", run_timestamp)
        logger.info("Forecast step: PT000H00M")
        logger.info("Raw landing directory: %s", run_dir)

        if target.exists() and not args.overwrite:
            raise FileExistsError(
                f"{target} already exists. "
                "Use --overwrite only if you intentionally want to replace it."
            )

        download_url = build_download_url(run_timestamp)
        download_info = download_grib(
            session,
            download_url,
            target,
            logger,
        )

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
        if target.exists():
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
