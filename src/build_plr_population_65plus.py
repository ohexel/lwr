from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


RAW_POPULATION_PATH = Path(
    "data/raw/population/2025-12-31/"
    "EWR_L21_202512E_Matrix.csv"
)
ACCEPTED_POPULATION_PATH = Path(
    "data/normalized/population/"
    "plr_population_65plus.parquet"
)
REJECTED_POPULATION_PATH = Path(
    "data/normalized/population/rejected/"
    "plr_population_65plus_rejected.parquet"
)
POPULATION_VALIDATION_SUMMARY_PATH = Path(
    "reports/profiling/population/"
    "validation_summary.json"
)

EXPECTED_SOURCE_PLR_COUNT = 542

SOURCE_COLUMNS = {
    "RAUMID",
    "E_E",
    "E_E65U80",
    "E_E80U110",
}


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def normalize_population_source(
    raw: pd.DataFrame,
) -> pd.DataFrame:
    missing = SOURCE_COLUMNS - set(raw.columns)
    if missing:
        raise ValueError(
            "Population source missing columns: "
            f"{sorted(missing)}"
        )

    result = pd.DataFrame(
        {
            "plr_id": raw["RAUMID"].astype("string"),
            "population_total": _to_numeric(raw["E_E"]),
            "population_65_79": _to_numeric(raw["E_E65U80"]),
            "population_80plus": _to_numeric(raw["E_E80U110"]),
        }
    )

    result["population_65plus"] = (
        result["population_65_79"]
        + result["population_80plus"]
    )
    result["share_65plus"] = (
        result["population_65plus"]
        / result["population_total"]
    )
    return result


def population_rejection_reason(
    row: pd.Series,
) -> str | None:
    if pd.isna(row["population_total"]):
        return "missing_population_total"

    if (
        pd.isna(row["population_65_79"])
        or pd.isna(row["population_80plus"])
    ):
        return "missing_population_65plus_component"

    if row["population_total"] < 0:
        return "negative_population_total"

    if (
        row["population_65plus"] < 0
        or row["population_65plus"]
        > row["population_total"]
    ):
        return "invalid_population_65plus"

    return None


def split_population_quality(
    normalized: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if len(normalized) != EXPECTED_SOURCE_PLR_COUNT:
        raise ValueError(
            "Population source must contain "
            f"{EXPECTED_SOURCE_PLR_COUNT} PLRs; "
            f"got {len(normalized)}"
        )

    if normalized["plr_id"].isna().any():
        raise ValueError(
            "Population source contains missing plr_id"
        )

    if normalized["plr_id"].duplicated().any():
        raise ValueError(
            "Population source contains duplicate PLR IDs"
        )

    work = normalized.copy()
    work["rejection_reason"] = work.apply(
        population_rejection_reason,
        axis=1,
    )

    accepted = work.loc[
        work["rejection_reason"].isna()
    ].drop(
        columns="rejection_reason"
    ).reset_index(drop=True)

    rejected = work.loc[
        work["rejection_reason"].notna()
    ].reset_index(drop=True)

    accepted_ids = set(
        accepted["plr_id"].astype(str)
    )
    rejected_ids = set(
        rejected["plr_id"].astype(str)
    )
    source_ids = set(
        work["plr_id"].astype(str)
    )

    if accepted_ids & rejected_ids:
        raise ValueError(
            "Accepted and rejected population PLR sets overlap"
        )

    if accepted_ids | rejected_ids != source_ids:
        raise ValueError(
            "Accepted + rejected population PLRs "
            "do not reconstruct the source set"
        )

    if len(accepted) + len(rejected) != len(work):
        raise ValueError(
            "Population quality split does not "
            "reconstruct source row count"
        )

    if accepted[
        [
            "population_total",
            "population_65plus",
            "share_65plus",
        ]
    ].isna().any().any():
        raise ValueError(
            "Accepted population contains missing "
            "business-critical values"
        )

    summary = {
        "source_row_count": int(len(work)),
        "accepted_row_count": int(len(accepted)),
        "rejected_row_count": int(len(rejected)),
        "rejection_reasons": {
            str(reason): int(count)
            for reason, count in rejected[
                "rejection_reason"
            ].value_counts().items()
        },
    }

    return accepted, rejected, summary


def write_population_quality_outputs(
    *,
    accepted: pd.DataFrame,
    rejected: pd.DataFrame,
    summary: dict,
    accepted_path: Path = ACCEPTED_POPULATION_PATH,
    rejected_path: Path = REJECTED_POPULATION_PATH,
    summary_path: Path = POPULATION_VALIDATION_SUMMARY_PATH,
) -> None:
    for path in (
        accepted_path,
        rejected_path,
        summary_path,
    ):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    accepted.to_parquet(
        accepted_path,
        index=False,
    )
    rejected.to_parquet(
        rejected_path,
        index=False,
    )
    summary_path.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def build_population_quality_outputs(
    raw_path: Path = RAW_POPULATION_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw = pd.read_csv(
        raw_path,
        sep=";",
        dtype={"RAUMID": "string"},
    )
    normalized = normalize_population_source(raw)
    accepted, rejected, summary = (
        split_population_quality(normalized)
    )
    write_population_quality_outputs(
        accepted=accepted,
        rejected=rejected,
        summary=summary,
    )
    return accepted, rejected, summary


if __name__ == "__main__":
    accepted, rejected, summary = (
        build_population_quality_outputs()
    )
    print(summary)
    if not rejected.empty:
        print()
        print(
            rejected[
                [
                    "plr_id",
                    "population_total",
                    "population_65plus",
                    "rejection_reason",
                ]
            ].to_string(index=False)
        )
