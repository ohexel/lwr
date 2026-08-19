from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.forecast_key import ForecastKey
from src.icon_d2_ruc_grib import DecodedIconField
from src.icon_d2_ruc_indicators import get_indicator


EXPECTED_ICON_D2_POINT_COUNT = 542_040


def build_normalized_icon_frame(
    *,
    indicator: str,
    forecast: ForecastKey,
    decoded: DecodedIconField,
) -> pd.DataFrame:
    """
    Convert one validated ICON D2 RUC field into the project's
    normalized tabular representation.

    Missing GRIB points must already have been normalized to NaN by the
    shared GRIB decoder.
    """
    contract = get_indicator(indicator)
    values = np.asarray(
        decoded.values,
        dtype="float64",
    )

    frame = pd.DataFrame(
        {
            "cell_index": np.arange(
                len(values),
                dtype="int64",
            ),
            "run_time_utc": pd.Timestamp(
                forecast.run_time
            ),
            "lead_time": (
                forecast.lead_time_label
            ),
            "valid_time_utc": pd.Timestamp(
                forecast.valid_time
            ),
            contract.output_column: values,
        }
    )

    if indicator == "T_2M":
        frame["temperature_c"] = (
            frame["temperature_k"] - 273.15
        )

    elif indicator == "TD_2M":
        frame["dew_point_temperature_c"] = (
            frame["dew_point_temperature_k"]
            - 273.15
        )

    return frame


def write_normalized_icon_frame(
    frame: pd.DataFrame,
    path: Path,
) -> None:
    """
    Write normalized Parquet atomically.

    A failed write must not leave a partial file at the canonical
    normalized-data path.
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    part_path = path.with_name(
        path.name + ".part"
    )

    if part_path.exists():
        part_path.unlink()

    try:
        frame.to_parquet(
            part_path,
            index=False,
        )
        part_path.replace(path)
    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise
