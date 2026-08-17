#!/usr/bin/env python3
"""
Download all three Berlin LOR hierarchy layers from the official WFS:

- Prognoseraum
- Bezirksregion
- Planungsraum

Outputs:
    <output-dir>/
        capabilities.xml
        lor_prognoseraum.geojson
        lor_bezirksregion.geojson
        lor_planungsraum.geojson
        metadata.json
        download.log

The script first discovers the WFS capabilities and advertised FeatureTypes.
It does not hard-code the actual WFS layer/type names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_WFS_URL = "https://gdi.berlin.de/services/wfs/lor_2021"

TARGET_LAYERS = {
    "prognoseraum": {
        "filename": "lor_prognoseraum.geojson",
        "aliases": ("prognoseraum", "prognoseraeume", "prg", "pgr"),
    },
    "bezirksregion": {
        "filename": "lor_bezirksregion.geojson",
        "aliases": ("bezirksregion", "bezirksregionen", "bzr"),
    },
    "planungsraum": {
        "filename": "lor_planungsraum.geojson",
        "aliases": ("planungsraum", "planungsraeume", "plr"),
    },
}

EXPECTED_FEATURE_COUNTS = {
    "prognoseraum": 58,
    "bezirksregion": 143,
    "planungsraum": 542,
}

REQUEST_TIMEOUT_SECONDS = 90


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise(text: str | None) -> str:
    """Lowercase text and transliterate German umlauts for matching."""
    text = text or ""
    text = (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ß", "ss")
    )
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def local_name(tag: str) -> str:
    """Strip an XML namespace from a tag."""
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, wanted_name: str) -> str | None:
    """Return the text of a direct child regardless of XML namespace."""
    for child in element:
        if local_name(child.tag) == wanted_name:
            return child.text.strip() if child.text else None
    return None


def parse_wfs_capabilities(xml_bytes: bytes) -> dict[str, Any]:
    """
    Parse capabilities without assuming a specific WFS XML namespace.

    Returns:
        {
            "version": "...",
            "operations": [...],
            "getfeature_output_formats": [...],
            "layers": [
                {"name": ..., "title": ..., "default_crs": ...},
                ...
            ]
        }
    """
    root = ET.fromstring(xml_bytes)

    version = root.attrib.get("version", "unknown")

    operations: list[str] = []
    for elem in root.iter():
        if local_name(elem.tag) == "Operation":
            name = elem.attrib.get("name")
            if name and name not in operations:
                operations.append(name)

    # WFS 1.x may advertise operations differently.
    if not operations:
        for elem in root.iter():
            name = local_name(elem.tag)
            if name in {"GetCapabilities", "DescribeFeatureType", "GetFeature"}:
                if name not in operations:
                    operations.append(name)

    output_formats: list[str] = []
    for elem in root.iter():
        if local_name(elem.tag) == "Parameter" and elem.attrib.get("name", "").lower() == "outputformat":
            for desc in elem.iter():
                if local_name(desc.tag) in {"Value", "DefaultValue"} and desc.text:
                    value = desc.text.strip()
                    if value and value not in output_formats:
                        output_formats.append(value)

    # Some WFS 1.x capabilities use ResultFormat elements.
    for elem in root.iter():
        if local_name(elem.tag) == "ResultFormat":
            for child in elem:
                value = local_name(child.tag)
                if value and value not in output_formats:
                    output_formats.append(value)

    layers: list[dict[str, str | None]] = []
    for elem in root.iter():
        if local_name(elem.tag) != "FeatureType":
            continue

        name = child_text(elem, "Name")
        title = child_text(elem, "Title")
        default_crs = (
            child_text(elem, "DefaultCRS")
            or child_text(elem, "DefaultSRS")
            or child_text(elem, "SRS")
        )

        if name:
            layers.append(
                {
                    "name": name,
                    "title": title,
                    "default_crs": default_crs,
                }
            )

    return {
        "version": version,
        "operations": operations,
        "getfeature_output_formats": output_formats,
        "layers": layers,
    }


def score_layer(layer: dict[str, Any], aliases: tuple[str, ...]) -> int:
    """
    Score how likely a WFS FeatureType is to represent one target LOR level.

    Exact/word matches score higher than loose substring matches.
    """
    name = normalise(layer.get("name"))
    title = normalise(layer.get("title"))
    combined = f"{name} {title}"

    score = 0
    for alias in aliases:
        alias_n = normalise(alias)

        if re.search(rf"(^|[^a-z0-9]){re.escape(alias_n)}([^a-z0-9]|$)", combined):
            score += 10
        elif alias_n in combined:
            score += 3

        if alias_n == name.split(":")[-1]:
            score += 10

    return score


def discover_target_layers(layers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Identify Prognoseraum, Bezirksregion, and Planungsraum from advertised layers.

    Raises an error if a target cannot be found or is ambiguous.
    """
    discovered: dict[str, dict[str, Any]] = {}

    for target, spec in TARGET_LAYERS.items():
        scored = [
            (score_layer(layer, spec["aliases"]), layer)
            for layer in layers
        ]
        scored = [(score, layer) for score, layer in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)

        if not scored:
            raise RuntimeError(
                f"Could not identify a WFS layer for '{target}'. "
                "Inspect the available layers in download.log."
            )

        best_score, best_layer = scored[0]

        if len(scored) > 1 and scored[1][0] == best_score:
            tied = [layer["name"] for score, layer in scored if score == best_score]
            raise RuntimeError(
                f"Ambiguous WFS layer match for '{target}': {tied}. "
                "Inspect GetCapabilities and adjust TARGET_LAYERS aliases."
            )

        discovered[target] = best_layer

    names = [layer["name"] for layer in discovered.values()]
    if len(names) != len(set(names)):
        raise RuntimeError(
            f"Layer discovery selected the same WFS layer more than once: {names}"
        )

    return discovered


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    tmp_path.replace(path)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "berlin-lor-capstone-downloader/1.0",
            "Accept": "*/*",
        }
    )
    return session


def get_capabilities(
    session: requests.Session,
    wfs_url: str,
) -> tuple[requests.Response, bytes]:
    response = session.get(
        wfs_url,
        params={
            "service": "WFS",
            "request": "GetCapabilities",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response, response.content


def geojson_candidates(advertised_formats: list[str]) -> list[str]:
    """
    Build a conservative list of GeoJSON output-format values.

    GeoServer installations commonly accept application/json. We try
    advertised JSON/GeoJSON formats first, followed by common fallbacks.
    """
    advertised_json = [
        value
        for value in advertised_formats
        if "json" in normalise(value)
    ]

    fallbacks = [
        "application/json",
        "json",
        "application/json; subtype=geojson",
        "geojson",
    ]

    result: list[str] = []
    for value in advertised_json + fallbacks:
        if value not in result:
            result.append(value)

    return result


def download_feature_type(
    session: requests.Session,
    wfs_url: str,
    wfs_version: str,
    feature_type: str,
    output_path: Path,
    advertised_formats: list[str],
    logger: logging.Logger,
) -> dict[str, Any]:
    """
    Download one WFS FeatureType as GeoJSON.

    Tries advertised/common GeoJSON output-format names until a valid
    FeatureCollection is returned.
    """
    type_parameter = "typeNames" if wfs_version.startswith("2") else "typeName"
    errors: list[str] = []

    for output_format in geojson_candidates(advertised_formats):
        params = {
            "service": "WFS",
            "version": wfs_version,
            "request": "GetFeature",
            type_parameter: feature_type,
            "outputFormat": output_format,
        }

        logger.info(
            "Trying GetFeature: feature_type=%s outputFormat=%s",
            feature_type,
            output_format,
        )

        try:
            response = session.get(
                wfs_url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            data = response.json()
            if data.get("type") != "FeatureCollection" or "features" not in data:
                raise ValueError(
                    "Response was JSON but not a GeoJSON FeatureCollection."
                )

            # Serialize ourselves so the raw file is guaranteed to be valid JSON.
            content = json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            atomic_write_bytes(output_path, content)

            return {
                "feature_type": feature_type,
                "output_format": output_format,
                "resolved_url": response.url,
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "feature_count": len(data["features"]),
                "file": output_path.name,
                "file_size_bytes": output_path.stat().st_size,
                "sha256": sha256_file(output_path),
            }

        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{output_format}: {exc}")
            logger.warning(
                "GetFeature attempt failed: feature_type=%s outputFormat=%s error=%s",
                feature_type,
                output_format,
                exc,
            )

    raise RuntimeError(
        f"Could not download '{feature_type}' as GeoJSON. "
        f"Attempts: {' | '.join(errors)}"
    )


def configure_logging(output_dir: Path) -> logging.Logger:
    logger = logging.getLogger("lor_downloader")
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

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download all Berlin LOR hierarchy layers from the official WFS."
    )
    parser.add_argument(
        "--wfs-url",
        default=os.getenv("WFS_URL", DEFAULT_WFS_URL),
        help=f"WFS endpoint (default: {DEFAULT_WFS_URL})",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("OUTPUT_DIR", "data/raw/berlin/lor"),
        help="Output directory (default: data/raw/berlin/lor)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(output_dir)

    started_at = utc_now()

    metadata: dict[str, Any] = {
        "dataset": "Lebensweltlich orientierte Räume (LOR) (01.01.2021)",
        "publisher": "Amt für Statistik Berlin-Brandenburg",
        "source_url": args.wfs_url,
        "retrieved_at": started_at,
        "license": "CC BY 3.0 DE",
        "attribution": (
            "Amt für Statistik Berlin-Brandenburg / "
            "Lebensweltlich orientierte Räume (LOR) (01.01.2021)"
        ),
        "capabilities": {},
        "available_layers": [],
        "downloaded_layers": [],
    }

    logger.info("Starting Berlin LOR WFS acquisition")
    logger.info("WFS endpoint: %s", args.wfs_url)
    logger.info("Output directory: %s", output_dir.resolve())

    try:
        session = make_session()

        response, capabilities_xml = get_capabilities(session, args.wfs_url)
        capabilities_path = output_dir / "capabilities.xml"
        atomic_write_bytes(capabilities_path, capabilities_xml)

        parsed = parse_wfs_capabilities(capabilities_xml)

        metadata["capabilities"] = {
            "request_url": response.url,
            "http_status": response.status_code,
            "wfs_version": parsed["version"],
            "operations": parsed["operations"],
            "getfeature_output_formats": parsed["getfeature_output_formats"],
            "file": capabilities_path.name,
            "file_size_bytes": capabilities_path.stat().st_size,
            "sha256": sha256_file(capabilities_path),
        }
        metadata["available_layers"] = parsed["layers"]

        logger.info("AVAILABLE CAPABILITIES")
        logger.info("WFS version: %s", parsed["version"])
        logger.info(
            "Operations: %s",
            ", ".join(parsed["operations"]) or "(none parsed)",
        )
        logger.info(
            "GetFeature output formats: %s",
            ", ".join(parsed["getfeature_output_formats"]) or "(none parsed)",
        )

        logger.info("AVAILABLE LAYERS (%d)", len(parsed["layers"]))
        for layer in parsed["layers"]:
            logger.info(
                "Layer: name=%s | title=%s | default_crs=%s",
                layer.get("name"),
                layer.get("title"),
                layer.get("default_crs"),
            )

        selected = discover_target_layers(parsed["layers"])

        logger.info("SELECTED LOR LAYERS")
        for target, layer in selected.items():
            logger.info(
                "%s -> name=%s | title=%s",
                target,
                layer.get("name"),
                layer.get("title"),
            )

        for target, layer in selected.items():
            output_path = output_dir / TARGET_LAYERS[target]["filename"]

            result = download_feature_type(
                session=session,
                wfs_url=args.wfs_url,
                wfs_version=parsed["version"],
                feature_type=layer["name"],
                output_path=output_path,
                advertised_formats=parsed["getfeature_output_formats"],
                logger=logger,
            )
            result["logical_layer"] = target
            result["title"] = layer.get("title")
            result["default_crs"] = layer.get("default_crs")
            result["expected_feature_count"] = EXPECTED_FEATURE_COUNTS[target]
            result["feature_count_matches_expected"] = (
                result["feature_count"] == EXPECTED_FEATURE_COUNTS[target]
            )
            metadata["downloaded_layers"].append(result)

            if result["feature_count_matches_expected"]:
                logger.info(
                    "DOWNLOADED %s: %s features -> %s",
                    target,
                    result["feature_count"],
                    output_path,
                )
            else:
                logger.warning(
                    "DOWNLOADED %s: %s features; expected %s -> %s",
                    target,
                    result["feature_count"],
                    EXPECTED_FEATURE_COUNTS[target],
                    output_path,
                )

        metadata["completed_at"] = utc_now()
        metadata["status"] = "success"

        metadata_path = output_dir / "metadata.json"
        atomic_write_bytes(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        )

        logger.info(
            "ACTUALLY DOWNLOADED LAYERS: %s",
            ", ".join(
                item["logical_layer"] for item in metadata["downloaded_layers"]
            ),
        )
        logger.info("Metadata written to %s", metadata_path)
        logger.info("Acquisition completed successfully")
        return 0

    except Exception as exc:
        metadata["completed_at"] = utc_now()
        metadata["status"] = "failed"
        metadata["error"] = str(exc)

        metadata_path = output_dir / "metadata.json"
        atomic_write_bytes(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        )

        logger.exception("Acquisition failed: %s", exc)
        logger.info("Failure metadata written to %s", metadata_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
