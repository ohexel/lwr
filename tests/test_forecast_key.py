from datetime import timedelta

import pytest

from src.forecast_key import (
    ForecastKey,
    ProjectPaths,
    parse_lead_time,
)


def test_forecast_key_derives_valid_time_for_zero_and_twelve_hours():
    lead_zero = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT000H00M",
    )
    lead_twelve = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT012H00M",
    )

    assert (
        lead_zero.valid_time.isoformat()
        == "2026-08-19T12:00:00+00:00"
    )
    assert (
        lead_twelve.valid_time.isoformat()
        == "2026-08-20T00:00:00+00:00"
    )


def test_forecast_key_and_paths_use_canonical_partition_identity():
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT012H00M",
    )
    paths = ProjectPaths()

    assert forecast.run_time_partition_key == "20260819T1200"
    assert forecast.lead_time_partition_key == "PT012H00M"

    assert str(
        paths.raw_icon_field(
            indicator="T_2M",
            forecast=forecast,
        )
    ) == (
        "data/raw/icon_d2_ruc/t_2m/"
        "20260819T1200/PT012H00M/t_2m.grib2"
    )

    assert str(
        paths.normalized_icon_field(
            indicator="RELHUM_2M",
            forecast=forecast,
        )
    ) == (
        "data/normalized/icon_d2_ruc/relhum_2m/"
        "20260819T1200/PT012H00M/relhum_2m.parquet"
    )


def test_invalid_lead_time_is_rejected():
    with pytest.raises(ValueError):
        parse_lead_time("12h")

    with pytest.raises(ValueError):
        parse_lead_time("PT012H99M")
