import numpy as np
import pytest

from src.forecast_key import ForecastKey
from src.icon_d2_ruc_grib import (
    DecodedIconField,
)
from src.normalize_icon_d2_ruc import (
    build_normalized_icon_frame,
)


def test_t_2m_normalization_preserves_forecast_identity_and_celsius():
    forecast = ForecastKey.from_dwd_labels(
        run_time="2026-08-19T12:00",
        lead_time="PT012H00M",
    )

    decoded = DecodedIconField(
        values=np.array(
            [273.15, 280.0, np.nan]
        ),
        metadata={},
    )

    frame = build_normalized_icon_frame(
        indicator="T_2M",
        forecast=forecast,
        decoded=decoded,
    )

    assert frame["cell_index"].tolist() == [
        0,
        1,
        2,
    ]
    assert frame["lead_time"].unique().tolist() == [
        "PT012H00M"
    ]
    assert (
        frame["run_time_utc"].iloc[0].isoformat()
        == "2026-08-19T12:00:00+00:00"
    )
    assert (
        frame["valid_time_utc"].iloc[0].isoformat()
        == "2026-08-20T00:00:00+00:00"
    )
    assert frame["temperature_c"].iloc[0] == pytest.approx(
        0.0
    )
    assert np.isnan(
        frame["temperature_c"].iloc[2]
    )
