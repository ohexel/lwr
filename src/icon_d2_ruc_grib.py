from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.forecast_key import ForecastKey
from src.icon_d2_ruc_indicators import (
    IconD2RucIndicator,
    get_indicator,
)


@dataclass(frozen=True)
class DecodedIconField:
    values: np.ndarray
    metadata: dict[str, Any]


def normalize_unit(value: object) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("·", "")
    )


def parse_grib_datetime(
    date_value: object,
    time_value: object,
) -> datetime | None:
    if date_value is None or time_value is None:
        return None

    date_text = str(int(date_value))
    time_text = f"{int(time_value):04d}"

    return datetime.strptime(
        date_text + time_text,
        "%Y%m%d%H%M",
    ).replace(tzinfo=timezone.utc)


def normalize_bitmap_missing_values(
    values: np.ndarray,
    *,
    number_of_missing: int,
    bitmap_present: int,
    missing_value: float,
) -> np.ndarray:
    """
    Convert ecCodes' decoder-side missing marker to NaN only when
    the GRIB bitmap reports missing values.

    A numeric value such as 9999 is not treated as missing merely
    because it equals ecCodes' default missingValue marker.
    """
    result = np.asarray(
        values,
        dtype="float64",
    ).copy()

    if number_of_missing == 0:
        return result

    if number_of_missing < 0:
        raise ValueError(
            "numberOfMissing cannot be negative"
        )

    if bitmap_present != 1:
        raise ValueError(
            "GRIB reports missing values without a bitmap"
        )

    missing_mask = result == float(missing_value)
    decoded_missing = int(missing_mask.sum())

    if decoded_missing != number_of_missing:
        raise ValueError(
            "GRIB missing-value metadata is inconsistent: "
            f"numberOfMissing={number_of_missing}, "
            f"decoded markers={decoded_missing}"
        )

    result[missing_mask] = np.nan
    return result


def _load_eccodes():
    try:
        from eccodes import (
            codes_get,
            codes_get_values,
            codes_grib_new_from_file,
            codes_release,
        )
    except ImportError as exc:
        raise RuntimeError(
            "ecCodes Python bindings are required to decode "
            "ICON D2 RUC GRIB2 files"
        ) from exc

    return {
        "codes_get": codes_get,
        "codes_get_values": codes_get_values,
        "codes_grib_new_from_file": (
            codes_grib_new_from_file
        ),
        "codes_release": codes_release,
    }


def _safe_get(
    codes_get,
    handle,
    key: str,
    default=None,
):
    try:
        return codes_get(handle, key)
    except Exception:
        return default


def extract_grib_field(
    path: Path,
) -> DecodedIconField:
    """
    Decode one GRIB2 field and normalize bitmap-defined missing
    values to NaN.

    Indicator-specific metadata validation happens separately so the
    low-level decoder remains reusable.
    """
    eccodes = _load_eccodes()
    codes_get = eccodes["codes_get"]
    codes_get_values = eccodes[
        "codes_get_values"
    ]
    codes_grib_new_from_file = eccodes[
        "codes_grib_new_from_file"
    ]
    codes_release = eccodes["codes_release"]

    with path.open("rb") as file_handle:
        handle = codes_grib_new_from_file(
            file_handle
        )

        if handle is None:
            raise RuntimeError(
                f"No GRIB message found in {path}"
            )

        try:
            values = np.asarray(
                codes_get_values(handle),
                dtype="float64",
            )

            metadata = {
                "edition": _safe_get(
                    codes_get,
                    handle,
                    "edition",
                ),
                "centre": _safe_get(
                    codes_get,
                    handle,
                    "centre",
                ),
                "shortName": _safe_get(
                    codes_get,
                    handle,
                    "shortName",
                ),
                "name": _safe_get(
                    codes_get,
                    handle,
                    "name",
                ),
                "units": _safe_get(
                    codes_get,
                    handle,
                    "units",
                ),
                "discipline": _safe_get(
                    codes_get,
                    handle,
                    "discipline",
                ),
                "parameterCategory": _safe_get(
                    codes_get,
                    handle,
                    "parameterCategory",
                ),
                "parameterNumber": _safe_get(
                    codes_get,
                    handle,
                    "parameterNumber",
                ),
                "typeOfFirstFixedSurface": (
                    _safe_get(
                        codes_get,
                        handle,
                        "typeOfFirstFixedSurface",
                    )
                ),
                "scaleFactorOfFirstFixedSurface": (
                    _safe_get(
                        codes_get,
                        handle,
                        "scaleFactorOfFirstFixedSurface",
                    )
                ),
                "scaledValueOfFirstFixedSurface": (
                    _safe_get(
                        codes_get,
                        handle,
                        "scaledValueOfFirstFixedSurface",
                    )
                ),
                "typeOfLevel": _safe_get(
                    codes_get,
                    handle,
                    "typeOfLevel",
                ),
                "level": _safe_get(
                    codes_get,
                    handle,
                    "level",
                ),
                "numberOfPoints": _safe_get(
                    codes_get,
                    handle,
                    "numberOfPoints",
                    len(values),
                ),
                "numberOfMissing": _safe_get(
                    codes_get,
                    handle,
                    "numberOfMissing",
                    0,
                ),
                "bitmapPresent": _safe_get(
                    codes_get,
                    handle,
                    "bitmapPresent",
                    0,
                ),
                "missingValue": _safe_get(
                    codes_get,
                    handle,
                    "missingValue",
                    9999.0,
                ),
                "gridType": _safe_get(
                    codes_get,
                    handle,
                    "gridType",
                ),
                "uuidOfHGrid": _safe_get(
                    codes_get,
                    handle,
                    "uuidOfHGrid",
                ),
                "forecastTime": _safe_get(
                    codes_get,
                    handle,
                    "forecastTime",
                ),
                "stepRange": _safe_get(
                    codes_get,
                    handle,
                    "stepRange",
                ),
            }

            run_time = parse_grib_datetime(
                _safe_get(
                    codes_get,
                    handle,
                    "dataDate",
                ),
                _safe_get(
                    codes_get,
                    handle,
                    "dataTime",
                ),
            )

            valid_time = parse_grib_datetime(
                _safe_get(
                    codes_get,
                    handle,
                    "validityDate",
                ),
                _safe_get(
                    codes_get,
                    handle,
                    "validityTime",
                ),
            )

            metadata["run_time_utc"] = (
                run_time.isoformat()
                if run_time is not None
                else None
            )
            metadata["valid_time_utc"] = (
                valid_time.isoformat()
                if valid_time is not None
                else None
            )

            number_missing = int(
                metadata["numberOfMissing"] or 0
            )
            bitmap_present = int(
                metadata["bitmapPresent"] or 0
            )
            missing_value = float(
                metadata["missingValue"]
            )

            values = (
                normalize_bitmap_missing_values(
                    values,
                    number_of_missing=(
                        number_missing
                    ),
                    bitmap_present=(
                        bitmap_present
                    ),
                    missing_value=missing_value,
                )
            )

            return DecodedIconField(
                values=values,
                metadata=metadata,
            )

        finally:
            codes_release(handle)


def validate_field_metadata(
    *,
    indicator: str,
    forecast: ForecastKey,
    metadata: dict[str, Any],
    expected_point_count: int | None = None,
) -> None:
    """
    Validate the source identity needed for safe normalization.

    This is intentionally limited to project-critical metadata:
    indicator identity, surface, units, forecast identity, point count,
    and GRIB missing-value structure.
    """
    contract = get_indicator(indicator)

    if int(metadata.get("edition", -1)) != 2:
        raise ValueError(
            f"{indicator} is not GRIB edition 2"
        )

    expected_pairs = {
        "discipline": contract.discipline,
        "parameterCategory": (
            contract.parameter_category
        ),
        "parameterNumber": (
            contract.parameter_number
        ),
        "typeOfFirstFixedSurface": (
            contract.first_surface_type
        ),
        "scaledValueOfFirstFixedSurface": (
            contract.first_surface_scaled_value
        ),
    }

    for key, expected in expected_pairs.items():
        actual = metadata.get(key)

        if (
            actual is None
            or int(actual) != int(expected)
        ):
            raise ValueError(
                f"{indicator} metadata mismatch "
                f"for {key}: {actual!r}; "
                f"expected {expected!r}"
            )

    actual_unit = normalize_unit(
        metadata.get("units")
    )
    allowed_units = {
        normalize_unit(value)
        for value in contract.allowed_units
    }

    if actual_unit not in allowed_units:
        raise ValueError(
            f"{indicator} has unexpected units "
            f"{metadata.get('units')!r}"
        )

    points = metadata.get("numberOfPoints")

    if (
        expected_point_count is not None
        and points is not None
        and int(points)
        != int(expected_point_count)
    ):
        raise ValueError(
            f"{indicator} has {int(points):,} "
            "GRIB points; expected "
            f"{int(expected_point_count):,}"
        )

    number_missing = int(
        metadata.get("numberOfMissing", 0) or 0
    )
    bitmap_present = int(
        metadata.get("bitmapPresent", 0) or 0
    )

    if (
        number_missing > 0
        and bitmap_present != 1
    ):
        raise ValueError(
            f"{indicator} reports missing values "
            "without a GRIB bitmap"
        )

    run_time_text = metadata.get(
        "run_time_utc"
    )
    valid_time_text = metadata.get(
        "valid_time_utc"
    )

    if run_time_text is None:
        raise ValueError(
            f"{indicator} GRIB has no run time"
        )

    if valid_time_text is None:
        raise ValueError(
            f"{indicator} GRIB has no valid time"
        )

    actual_run_time = datetime.fromisoformat(
        str(run_time_text)
    ).astimezone(timezone.utc)

    actual_valid_time = datetime.fromisoformat(
        str(valid_time_text)
    ).astimezone(timezone.utc)

    if actual_run_time != forecast.run_time:
        raise ValueError(
            f"{indicator} run time does not match "
            "the requested forecast partition"
        )

    if actual_valid_time != forecast.valid_time:
        raise ValueError(
            f"{indicator} valid time does not match "
            "run_time + lead_time"
        )


def decode_and_validate_field(
    *,
    path: Path,
    indicator: str,
    forecast: ForecastKey,
    expected_point_count: int | None = None,
) -> DecodedIconField:
    decoded = extract_grib_field(path)

    validate_field_metadata(
        indicator=indicator,
        forecast=forecast,
        metadata=decoded.metadata,
        expected_point_count=(
            expected_point_count
        ),
    )

    return decoded
