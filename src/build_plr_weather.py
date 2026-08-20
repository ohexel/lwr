from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from src.forecast_key import ForecastKey, ProjectPaths

EXPECTED_PLR_COUNT = 542
BRIDGE_WEIGHT_COLUMN = "fraction_of_plr"
INDICATOR_VALUE_COLUMNS = {
    "T_2M": "temperature_c",
    "RELHUM_2M": "relative_humidity_percent",
    "TD_2M": "dew_point_temperature_c",
    "U_10M": "wind_u_10m_ms",
    "V_10M": "wind_v_10m_ms",
}


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    """Area-weighted mean that preserves missing source values."""
    if values.isna().any() or weights.isna().any():
        return float("nan")
    denominator = float(weights.sum())
    if denominator <= 0:
        return float("nan")
    return float(np.average(values.to_numpy(dtype="float64"), weights=weights.to_numpy(dtype="float64")))


def combine_icon_weather_fields(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Join the five normalized source fields and derive 10 m wind speed."""
    missing_indicators = set(INDICATOR_VALUE_COLUMNS) - set(frames)
    if missing_indicators:
        raise ValueError(f"Missing normalized weather frames: {sorted(missing_indicators)}")

    keys = ["cell_index", "run_time_utc", "lead_time", "valid_time_utc"]
    combined = None
    for indicator, value_column in INDICATOR_VALUE_COLUMNS.items():
        frame = frames[indicator]
        required = set(keys + [value_column])
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{indicator} frame missing columns: {sorted(missing)}")
        current = frame[keys + [value_column]].copy()
        if current["cell_index"].duplicated().any():
            raise ValueError(f"{indicator} contains duplicate cell_index values")
        combined = current if combined is None else combined.merge(current, on=keys, how="inner", validate="one_to_one")

    expected_rows = len(frames["T_2M"])
    if len(combined) != expected_rows:
        raise ValueError(
            "Normalized weather fields do not share the same complete cell/forecast identity: "
            f"combined_rows={len(combined):,}, expected_rows={expected_rows:,}"
        )
    combined["wind_speed_10m_ms"] = np.sqrt(
        np.square(combined["wind_u_10m_ms"]) + np.square(combined["wind_v_10m_ms"])
    )
    return combined


def aggregate_weather_to_plr(*, weather: pd.DataFrame, bridge: pd.DataFrame) -> pd.DataFrame:
    """Area-weight ICON cell weather onto Berlin PLRs using fraction_of_plr."""
    bridge_required = {"plr_id", "cell_index", BRIDGE_WEIGHT_COLUMN}
    missing_bridge = bridge_required - set(bridge.columns)
    if missing_bridge:
        raise ValueError(f"Bridge missing required columns: {sorted(missing_bridge)}")

    value_columns = [
        "temperature_c",
        "relative_humidity_percent",
        "dew_point_temperature_c",
        "wind_u_10m_ms",
        "wind_v_10m_ms",
        "wind_speed_10m_ms",
    ]
    weather_required = {"cell_index", "run_time_utc", "lead_time", "valid_time_utc", *value_columns}
    missing_weather = weather_required - set(weather.columns)
    if missing_weather:
        raise ValueError(f"Weather missing required columns: {sorted(missing_weather)}")

    joined = bridge[["plr_id", "cell_index", BRIDGE_WEIGHT_COLUMN]].merge(
        weather[["cell_index", *value_columns]], on="cell_index", how="left", validate="many_to_one", indicator=True
    )
    unmatched = joined.loc[joined["_merge"] != "both", "cell_index"].drop_duplicates()
    if not unmatched.empty:
        raise ValueError(f"Bridge references ICON cells absent from normalized weather: {unmatched.head(10).tolist()}")

    identity = weather[["run_time_utc", "lead_time", "valid_time_utc"]].drop_duplicates()
    if len(identity) != 1:
        raise ValueError("Combined weather does not contain exactly one forecast identity")

    rows=[]
    for plr_id, group in joined.groupby("plr_id", sort=True):
        row={"plr_id": str(plr_id)}
        for column in value_columns:
            row[column]=weighted_mean(group[column], group[BRIDGE_WEIGHT_COLUMN])
        rows.append(row)
    result=pd.DataFrame(rows)
    if len(result) != EXPECTED_PLR_COUNT:
        raise ValueError(f"PLR weather output must contain {EXPECTED_PLR_COUNT} PLRs; got {len(result)}")

    i=identity.iloc[0]
    result["run_time_utc"]=i["run_time_utc"]
    result["lead_time"]=i["lead_time"]
    result["valid_time_utc"]=i["valid_time_utc"]
    return result[["plr_id", "run_time_utc", "lead_time", "valid_time_utc", *value_columns]]


def build_plr_weather(*, forecast: ForecastKey, paths: ProjectPaths | None = None,
                      bridge_path: Path = Path("data/normalized/icon_d2_grid/""icon_plr_area_bridge.parquet")) -> pd.DataFrame:
    project_paths = paths or ProjectPaths()
    frames = {
        indicator: pd.read_parquet(project_paths.normalized_icon_field(indicator=indicator, forecast=forecast))
        for indicator in INDICATOR_VALUE_COLUMNS
    }
    combined = combine_icon_weather_fields(frames)
    bridge = pd.read_parquet(bridge_path)
    return aggregate_weather_to_plr(weather=combined, bridge=bridge)


def write_plr_weather(*, frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part_path = path.with_name(path.name + ".part")
    if part_path.exists():
        part_path.unlink()
    try:
        frame.to_parquet(part_path, index=False)
        part_path.replace(path)
    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise
