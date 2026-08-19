from __future__ import annotations
from pathlib import Path
import pandas as pd
import pytest

from src.profile_afs_population import profile_afs_population
from src.validate_afs_population import (
    EXPECTED_PLR_COUNT,
    EXPECTED_REFERENCE_CODE,
    normalize_population_columns,
    add_population_65plus,
    validate_population_contract,
)

POPULATION_FILE = Path("data/raw/population/2025-12-31/EWR_L21_202512E_Matrix.csv")

def make_valid_population() -> pd.DataFrame:
    return pd.DataFrame({
        "RAUMID": ["A", "B"],
        "E_E": ["100", "200"],
        "E_E65U80": ["15", "20"],
        "E_E80U110": ["5", "10"],
        "ZEIT": ["202512", "202512"],
    })

def test_normalize_population_columns_uses_nullable_integers():
    population = make_valid_population()
    population.loc[1, "E_E80U110"] = None
    result = normalize_population_columns(population)
    assert str(result["E_E"].dtype) == "Int64"
    assert str(result["E_E65U80"].dtype) == "Int64"
    assert str(result["E_E80U110"].dtype) == "Int64"
    assert pd.isna(result.loc[1, "E_E80U110"])

def test_add_population_65plus():
    result = add_population_65plus(normalize_population_columns(make_valid_population()))
    assert result.loc[0, "population_65plus"] == 20
    assert result.loc[1, "population_65plus"] == 30
    assert result.loc[0, "share_65plus"] == pytest.approx(0.20)
    assert result.loc[1, "share_65plus"] == pytest.approx(0.15)

def test_add_population_65plus_preserves_missing_component():
    population = make_valid_population()
    population.loc[1, "E_E80U110"] = None
    result = add_population_65plus(normalize_population_columns(population))
    assert pd.isna(result.loc[1, "population_65plus"])
    assert pd.isna(result.loc[1, "share_65plus"])

def test_validate_population_contract_accepts_valid_small_dataset():
    validate_population_contract(make_valid_population(), expected_plr_count=2, expected_reference_code="202512")

def test_validate_population_contract_allows_missing_age_component():
    population = make_valid_population()
    population.loc[1, "E_E80U110"] = None
    validate_population_contract(population, expected_plr_count=2, expected_reference_code="202512")

def test_validate_population_contract_rejects_duplicate_raumid():
    population = make_valid_population()
    population.loc[1, "RAUMID"] = "A"
    with pytest.raises(ValueError, match="duplicate RAUMID"):
        validate_population_contract(population, expected_plr_count=2, expected_reference_code="202512")

def test_validate_population_contract_rejects_missing_total_population():
    population = make_valid_population()
    population.loc[0, "E_E"] = None
    with pytest.raises(ValueError, match="missing total population"):
        validate_population_contract(population, expected_plr_count=2, expected_reference_code="202512")

def test_validate_population_contract_rejects_age_group_above_total():
    population = make_valid_population()
    population.loc[0, "E_E65U80"] = "101"
    with pytest.raises(ValueError, match="E_E65U80 exceeds E_E"):
        validate_population_contract(population, expected_plr_count=2, expected_reference_code="202512")

def test_validate_population_contract_rejects_wrong_reference_code():
    population = make_valid_population()
    population["ZEIT"] = "202411"
    with pytest.raises(ValueError, match="Unexpected population reference code"):
        validate_population_contract(population, expected_plr_count=2, expected_reference_code="202512")

def test_profile_afs_population_reports_missing_65plus():
    population = make_valid_population()
    population.loc[1, "E_E80U110"] = None
    profile = profile_afs_population(population)
    assert profile["null_counts"]["E_E80U110"] == 1
    assert profile["null_counts"]["population_65plus"] == 1
    assert profile["rows_with_incomplete_65plus"][0]["RAUMID"] == "B"

@pytest.fixture(scope="module")
def real_population() -> pd.DataFrame:
    if not POPULATION_FILE.exists():
        pytest.skip(f"AfS fixture not available: {POPULATION_FILE}")
    return pd.read_csv(POPULATION_FILE, sep=";", dtype=str)

def test_real_population_contract(real_population: pd.DataFrame):
    validate_population_contract(
        real_population,
        expected_plr_count=EXPECTED_PLR_COUNT,
        expected_reference_code=EXPECTED_REFERENCE_CODE,
    )

def test_real_population_has_expected_542_plrs(real_population: pd.DataFrame):
    normalized = normalize_population_columns(real_population)
    assert len(normalized) == 542
    assert normalized["RAUMID"].nunique() == 542

def test_real_population_known_missing_80plus_rows(real_population: pd.DataFrame):
    normalized = normalize_population_columns(real_population)
    missing_ids = set(normalized.loc[normalized["E_E80U110"].isna(), "RAUMID"].astype(str))
    assert missing_ids == {"03400831", "06200418"}

def test_real_population_65plus_missingness_matches_source(real_population: pd.DataFrame):
    derived = add_population_65plus(normalize_population_columns(real_population))
    missing_ids = set(derived.loc[derived["population_65plus"].isna(), "RAUMID"].astype(str))
    assert missing_ids == {"03400831", "06200418"}

def test_real_population_reference_code(real_population: pd.DataFrame):
    normalized = normalize_population_columns(real_population)
    assert normalized["ZEIT"].nunique() == 1
    assert normalized["ZEIT"].iloc[0] == "202512"
