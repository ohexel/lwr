import json
from pathlib import Path
from typing import Any

import dagster as dg
import numpy as np

from src.dagster_pipeline.partitions import (
    WEATHER_PARTITIONS,
    forecast_key_from_partition,
)
from src.dwd_icon_d2_ruc import (
    download_field,
    field_url,
    make_session,
    sha256_file,
    utc_now,
)
from src.forecast_key import (
    ForecastKey,
    ProjectPaths,
)
from src.icon_d2_ruc_grib import (
    decode_and_validate_field,
)
from src.icon_d2_ruc_indicators import (
    INDICATORS,
    get_indicator,
)
from src.normalize_icon_d2_ruc import (
    EXPECTED_ICON_D2_POINT_COUNT,
    build_normalized_icon_frame,
    write_normalized_icon_frame,
)


RAW_GROUP = "icon_d2_ruc_raw"
NORMALIZED_GROUP = "icon_d2_ruc_normalized"


def _forecast_from_context(
    context: dg.AssetExecutionContext,
) -> ForecastKey:
    partition_key = context.partition_key

    if not isinstance(
        partition_key,
        dg.MultiPartitionKey,
    ):
        raise ValueError(
            "ICON D2 RUC weather assets require "
            "a two-dimensional weather partition"
        )

    return forecast_key_from_partition(
        partition_key
    )


def _write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def _raw_sidecar_path(
    raw_path: Path,
) -> Path:
    return (
        raw_path.parent
        / "download_metadata.json"
    )


def _normalized_sidecar_path(
    normalized_path: Path,
) -> Path:
    return (
        normalized_path.parent
        / "grib_metadata.json"
    )


def _existing_raw_metadata(
    *,
    indicator: str,
    forecast: ForecastKey,
    raw_path: Path,
) -> dict[str, Any]:
    """
    Minimal metadata for a raw file that already exists locally.

    Existing acquisition metadata is preserved if a sidecar already
    exists; this fallback is used for Architecture 1 files or other
    retained raw files that pre-date the Dagster asset.
    """
    contract = get_indicator(indicator)

    return {
        "indicator": indicator,
        "dwd_parameter_id": (
            contract.dwd_parameter_id
        ),
        "run_time_utc": (
            forecast.run_time.isoformat()
        ),
        "lead_time": (
            forecast.lead_time_label
        ),
        "valid_time_utc": (
            forecast.valid_time.isoformat()
        ),
        "requested_url": field_url(
            indicator,
            forecast,
        ),
        "size_bytes": raw_path.stat().st_size,
        "sha256": sha256_file(raw_path),
        "acquisition_status": (
            "retained_raw_reused"
        ),
        "observed_at_utc": utc_now(),
    }


def _materialize_raw_field(
    context: dg.AssetExecutionContext,
    *,
    indicator: str,
) -> dg.MaterializeResult:
    forecast = _forecast_from_context(
        context
    )
    paths = ProjectPaths()
    raw_path = paths.raw_icon_field(
        indicator=indicator,
        forecast=forecast,
    )
    sidecar_path = _raw_sidecar_path(
        raw_path
    )

    if raw_path.exists():
        context.log.info(
            "Reusing retained raw %s for %s × %s",
            indicator,
            forecast.run_label,
            forecast.lead_time_label,
        )

        if sidecar_path.exists():
            try:
                download_metadata = json.loads(
                    sidecar_path.read_text(
                        encoding="utf-8"
                    )
                )
            except json.JSONDecodeError:
                download_metadata = (
                    _existing_raw_metadata(
                        indicator=indicator,
                        forecast=forecast,
                        raw_path=raw_path,
                    )
                )
        else:
            download_metadata = (
                _existing_raw_metadata(
                    indicator=indicator,
                    forecast=forecast,
                    raw_path=raw_path,
                )
            )
            _write_json(
                sidecar_path,
                download_metadata,
            )

        acquisition_status = (
            "retained_raw_reused"
        )

    else:
        context.log.info(
            "Downloading %s for %s × %s",
            indicator,
            forecast.run_label,
            forecast.lead_time_label,
        )

        with make_session() as session:
            download_metadata = (
                download_field(
                    session,
                    indicator=indicator,
                    forecast=forecast,
                    target=raw_path,
                )
            )

        download_metadata[
            "acquisition_status"
        ] = "downloaded"

        _write_json(
            sidecar_path,
            download_metadata,
        )

        acquisition_status = "downloaded"

    return dg.MaterializeResult(
        metadata={
            "indicator": indicator,
            "run_time": (
                forecast.run_time.isoformat()
            ),
            "lead_time": (
                forecast.lead_time_label
            ),
            "valid_time": (
                forecast.valid_time.isoformat()
            ),
            "path": str(raw_path),
            "size_bytes": (
                raw_path.stat().st_size
            ),
            "acquisition_status": (
                acquisition_status
            ),
            "source_url": field_url(
                indicator,
                forecast,
            ),
        }
    )


def _materialize_normalized_field(
    context: dg.AssetExecutionContext,
    *,
    indicator: str,
) -> dg.MaterializeResult:
    forecast = _forecast_from_context(
        context
    )
    paths = ProjectPaths()

    raw_path = paths.raw_icon_field(
        indicator=indicator,
        forecast=forecast,
    )
    normalized_path = (
        paths.normalized_icon_field(
            indicator=indicator,
            forecast=forecast,
        )
    )

    if not raw_path.exists():
        raise FileNotFoundError(
            "Expected raw ICON D2 RUC field "
            f"does not exist: {raw_path}"
        )

    context.log.info(
        "Decoding and normalizing %s for %s × %s",
        indicator,
        forecast.run_label,
        forecast.lead_time_label,
    )

    decoded = decode_and_validate_field(
        path=raw_path,
        indicator=indicator,
        forecast=forecast,
        expected_point_count=(
            EXPECTED_ICON_D2_POINT_COUNT
        ),
    )

    frame = build_normalized_icon_frame(
        indicator=indicator,
        forecast=forecast,
        decoded=decoded,
    )

    if len(frame) != (
        EXPECTED_ICON_D2_POINT_COUNT
    ):
        raise ValueError(
            f"{indicator} normalized frame has "
            f"{len(frame):,} rows; expected "
            f"{EXPECTED_ICON_D2_POINT_COUNT:,}"
        )

    write_normalized_icon_frame(
        frame,
        normalized_path,
    )

    metadata_path = (
        _normalized_sidecar_path(
            normalized_path
        )
    )
    _write_json(
        metadata_path,
        decoded.metadata,
    )

    contract = get_indicator(indicator)
    values = frame[
        contract.output_column
    ].to_numpy(dtype="float64")

    missing_count = int(
        np.isnan(values).sum()
    )

    return dg.MaterializeResult(
        metadata={
            "indicator": indicator,
            "run_time": (
                forecast.run_time.isoformat()
            ),
            "lead_time": (
                forecast.lead_time_label
            ),
            "valid_time": (
                forecast.valid_time.isoformat()
            ),
            "path": str(
                normalized_path
            ),
            "row_count": len(frame),
            "missing_count": missing_count,
            "output_column": (
                contract.output_column
            ),
        }
    )


def _build_indicator_assets(
    indicator: str,
) -> tuple[
    dg.AssetsDefinition,
    dg.AssetsDefinition,
]:
    """
    Build two distinct Dagster assets for one DWD source field.

    The factory removes repetitive Dagster wrapper code while keeping
    every source indicator visible as its own raw and normalized asset.
    """
    get_indicator(indicator)

    indicator_dir = (
        ProjectPaths.indicator_directory(
            indicator
        )
    )
    raw_name = f"raw_icon_{indicator_dir}"
    normalized_name = (
        f"normalized_icon_{indicator_dir}"
    )

    @dg.asset(
        name=raw_name,
        partitions_def=WEATHER_PARTITIONS,
        group_name=RAW_GROUP,
        compute_kind="python",
        description=(
            f"Retained raw DWD ICON D2 RUC "
            f"{indicator} GRIB2 field."
        ),
    )
    def raw_asset(
        context: dg.AssetExecutionContext,
    ) -> dg.MaterializeResult:
        return _materialize_raw_field(
            context,
            indicator=indicator,
        )

    @dg.asset(
        name=normalized_name,
        deps=[raw_asset],
        partitions_def=WEATHER_PARTITIONS,
        group_name=NORMALIZED_GROUP,
        compute_kind="python",
        description=(
            f"Decoded and normalized DWD "
            f"ICON D2 RUC {indicator} field."
        ),
    )
    def normalized_asset(
        context: dg.AssetExecutionContext,
    ) -> dg.MaterializeResult:
        return _materialize_normalized_field(
            context,
            indicator=indicator,
        )

    return raw_asset, normalized_asset


ICON_D2_RUC_ASSET_PAIRS = {
    indicator: _build_indicator_assets(
        indicator
    )
    for indicator in INDICATORS
}


RAW_ICON_D2_RUC_ASSETS = [
    pair[0]
    for pair in (
        ICON_D2_RUC_ASSET_PAIRS.values()
    )
]

NORMALIZED_ICON_D2_RUC_ASSETS = [
    pair[1]
    for pair in (
        ICON_D2_RUC_ASSET_PAIRS.values()
    )
]

ALL_ICON_D2_RUC_ASSETS = [
    asset
    for pair in (
        ICON_D2_RUC_ASSET_PAIRS.values()
    )
    for asset in pair
]
