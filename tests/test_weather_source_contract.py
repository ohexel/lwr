from src.icon_d2_ruc_indicators import INDICATORS


def test_required_weather_source_contract_is_minimal() -> None:
    assert tuple(INDICATORS) == (
        "T_2M",
        "RELHUM_2M",
        "U_10M",
        "V_10M",
    )
