from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IconD2RucIndicator:
    name: str
    output_column: str
    discipline: int
    parameter_category: int
    parameter_number: int
    first_surface_type: int
    first_surface_scaled_value: int
    allowed_units: frozenset[str]
    dwd_parameter_id: int


INDICATORS: dict[str, IconD2RucIndicator] = {
    "T_2M": IconD2RucIndicator(
        name="T_2M",
        output_column="temperature_k",
        discipline=0,
        parameter_category=0,
        parameter_number=0,
        first_surface_type=103,
        first_surface_scaled_value=2,
        allowed_units=frozenset({"k", "kelvin"}),
        dwd_parameter_id=500011,
    ),
    "RELHUM_2M": IconD2RucIndicator(
        name="RELHUM_2M",
        output_column="relative_humidity_percent",
        discipline=0,
        parameter_category=1,
        parameter_number=1,
        first_surface_type=103,
        first_surface_scaled_value=2,
        allowed_units=frozenset({"%", "percent"}),
        dwd_parameter_id=500036,
    ),
    "U_10M": IconD2RucIndicator(
        name="U_10M",
        output_column="wind_u_10m_ms",
        discipline=0,
        parameter_category=2,
        parameter_number=2,
        first_surface_type=103,
        first_surface_scaled_value=10,
        allowed_units=frozenset(
            {
                "m/s",
                "ms-1",
                "ms**-1",
                "ms^-1",
            }
        ),
        dwd_parameter_id=500027,
    ),
    "V_10M": IconD2RucIndicator(
        name="V_10M",
        output_column="wind_v_10m_ms",
        discipline=0,
        parameter_category=2,
        parameter_number=3,
        first_surface_type=103,
        first_surface_scaled_value=10,
        allowed_units=frozenset(
            {
                "m/s",
                "ms-1",
                "ms**-1",
                "ms^-1",
            }
        ),
        dwd_parameter_id=500029,
    ),
}


def get_indicator(name: str) -> IconD2RucIndicator:
    try:
        return INDICATORS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported ICON D2 RUC indicator: {name!r}"
        ) from exc
