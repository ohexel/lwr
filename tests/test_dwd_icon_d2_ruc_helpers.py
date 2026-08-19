from datetime import timedelta

import numpy as np
import pytest

from src.dwd_icon_d2_ruc import field_url
from src.forecast_key import ForecastKey
from src.icon_d2_ruc_grib import (
    normalize_bitmap_missing_values,
    validate_field_metadata,
)


def test_field_url_uses_run_and_lead_time():
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT012H00M",
    )

    assert field_url(
        "T_2M",
        forecast,
    ) == (
        "https://opendata.dwd.de/weather/nwp/v1/m/"
        "icon-d2-ruc/p/T_2M/r/"
        "2026-08-19T12:00/s/PT012H00M.grib2"
    )


def test_metadata_validation_accepts_twelve_hour_forecast():
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT012H00M",
    )

    metadata = {
        "edition": 2,
        "units": "K",
        "discipline": 0,
        "parameterCategory": 0,
        "parameterNumber": 0,
        "typeOfFirstFixedSurface": 103,
        "scaledValueOfFirstFixedSurface": 2,
        "numberOfPoints": 542040,
        "numberOfMissing": 0,
        "bitmapPresent": 0,
        "run_time_utc": (
            "2026-08-19T12:00:00+00:00"
        ),
        "valid_time_utc": (
            "2026-08-20T00:00:00+00:00"
        ),
    }

    validate_field_metadata(
        indicator="T_2M",
        forecast=forecast,
        metadata=metadata,
        expected_point_count=542040,
    )


def test_bitmap_missing_marker_is_only_normalized_when_bitmap_reports_missing():
    values = np.array(
        [280.0, 9999.0, 282.0]
    )

    normalized = (
        normalize_bitmap_missing_values(
            values,
            number_of_missing=1,
            bitmap_present=1,
            missing_value=9999.0,
        )
    )

    assert normalized[0] == 280.0
    assert np.isnan(normalized[1])
    assert normalized[2] == 282.0

    untouched = (
        normalize_bitmap_missing_values(
            values,
            number_of_missing=0,
            bitmap_present=0,
            missing_value=9999.0,
        )
    )

    assert untouched[1] == 9999.0
