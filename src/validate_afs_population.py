from __future__ import annotations
import pandas as pd

EXPECTED_PLR_COUNT = 542
EXPECTED_REFERENCE_CODE = "202512"
REQUIRED_COLUMNS = {"RAUMID", "E_E", "E_E65U80", "E_E80U110", "ZEIT"}

def normalize_population_columns(population: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(population.columns)
    if missing:
        raise ValueError(f"Population data is missing required columns: {sorted(missing)}")
    result = population.copy()
    result["RAUMID"] = result["RAUMID"].astype("string").str.strip()
    result["ZEIT"] = result["ZEIT"].astype("string").str.strip()
    for column in ["E_E", "E_E65U80", "E_E80U110"]:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("Int64")
    return result

def add_population_65plus(population: pd.DataFrame) -> pd.DataFrame:
    result = population.copy()
    result["population_65plus"] = (result["E_E65U80"] + result["E_E80U110"]).astype("Int64")
    result["share_65plus"] = result["population_65plus"] / result["E_E"]
    return result

def validate_population_contract(
    population: pd.DataFrame,
    *,
    expected_plr_count: int | None = EXPECTED_PLR_COUNT,
    expected_reference_code: str | None = EXPECTED_REFERENCE_CODE,
) -> None:
    normalized = normalize_population_columns(population)

    if expected_plr_count is not None and len(normalized) != expected_plr_count:
        raise ValueError(f"Unexpected population row count: {len(normalized):,}; expected {expected_plr_count:,}")
    if normalized["RAUMID"].isna().any() or normalized["RAUMID"].eq("").any():
        raise ValueError("Population data contains null/blank RAUMID values")
    if not normalized["RAUMID"].is_unique:
        raise ValueError("Population data contains duplicate RAUMID values")
    if normalized["ZEIT"].isna().any():
        raise ValueError("Population data contains null ZEIT values")
    if normalized["ZEIT"].nunique(dropna=False) != 1:
        raise ValueError("Population file contains more than one ZEIT reference code")
    if expected_reference_code is not None and str(normalized["ZEIT"].iloc[0]) != expected_reference_code:
        raise ValueError(
            f"Unexpected population reference code: {normalized['ZEIT'].iloc[0]}; "
            f"expected {expected_reference_code}"
        )

    if normalized["E_E"].isna().any():
        raise ValueError("Population data contains missing total population E_E")
    if (normalized["E_E"] < 0).any():
        raise ValueError("Population data contains negative total population")

    for column in ["E_E65U80", "E_E80U110"]:
        observed = normalized[column].dropna()
        if (observed < 0).any():
            raise ValueError(f"Population data contains negative values in {column}")
        comparable = normalized[normalized[column].notna()]
        if (comparable[column] > comparable["E_E"]).any():
            raise ValueError(f"{column} exceeds E_E for at least one PLR")

    derived = add_population_65plus(normalized)
    comparable = derived[derived["population_65plus"].notna()]
    if (comparable["population_65plus"] > comparable["E_E"]).any():
        raise ValueError("Derived population_65plus exceeds total population for at least one PLR")
    if (((comparable["share_65plus"] < 0) | (comparable["share_65plus"] > 1))).any():
        raise ValueError("Derived share_65plus falls outside [0, 1]")
