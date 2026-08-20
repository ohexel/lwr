import numpy as np
import pandas as pd
import pytest
from src.build_plr_weather import combine_icon_weather_fields, weighted_mean


def test_weighted_mean_area_weighting():
    assert weighted_mean(pd.Series([20.0,24.0]), pd.Series([0.25,0.75])) == pytest.approx(23.0)


def test_wind_speed_is_derived_from_u_and_v():
    identity = {
        "run_time_utc": pd.Timestamp("2026-08-19T17:00:00Z"),
        "lead_time": "PT000H00M",
        "valid_time_utc": pd.Timestamp("2026-08-19T17:00:00Z"),
    }
    def frame(column, values):
        return pd.DataFrame({"cell_index":[0,1], **{k:[v,v] for k,v in identity.items()}, column:values})
    combined=combine_icon_weather_fields({
        "T_2M": frame("temperature_c", [20.0,21.0]),
        "RELHUM_2M": frame("relative_humidity_percent", [50.0,55.0]),
        "TD_2M": frame("dew_point_temperature_c", [10.0,11.0]),
        "U_10M": frame("wind_u_10m_ms", [3.0,5.0]),
        "V_10M": frame("wind_v_10m_ms", [4.0,12.0]),
    })
    assert combined["wind_speed_10m_ms"].tolist() == pytest.approx([5.0,13.0])


def test_weighted_mean_preserves_missingness():
    assert np.isnan(weighted_mean(pd.Series([20.0,np.nan]), pd.Series([0.25,0.75])))
