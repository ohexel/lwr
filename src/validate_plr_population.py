from pathlib import Path

import pandas as pd

from src.build_plr_population_65plus import (
    ACCEPTED_POPULATION_PATH,
    EXPECTED_SOURCE_PLR_COUNT,
    REJECTED_POPULATION_PATH,
)


def validate_population_quality_artifacts(
    accepted: pd.DataFrame,
    rejected: pd.DataFrame,
) -> dict:
    accepted_required = {
        "plr_id",
        "population_total",
        "population_65plus",
        "share_65plus",
    }
    rejected_required = (
        accepted_required
        | {"rejection_reason"}
    )

    missing_accepted = (
        accepted_required - set(accepted.columns)
    )
    missing_rejected = (
        rejected_required - set(rejected.columns)
    )

    if missing_accepted:
        raise ValueError(
            "Accepted population missing columns: "
            f"{sorted(missing_accepted)}"
        )
    if missing_rejected:
        raise ValueError(
            "Rejected population missing columns: "
            f"{sorted(missing_rejected)}"
        )

    if accepted["plr_id"].duplicated().any():
        raise ValueError(
            "Accepted population contains duplicate PLR IDs"
        )
    if rejected["plr_id"].duplicated().any():
        raise ValueError(
            "Rejected population contains duplicate PLR IDs"
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

    accepted_ids = set(
        accepted["plr_id"].astype(str)
    )
    rejected_ids = set(
        rejected["plr_id"].astype(str)
    )

    if accepted_ids & rejected_ids:
        raise ValueError(
            "Accepted and rejected population PLR sets overlap"
        )

    total_rows = len(accepted) + len(rejected)
    if total_rows != EXPECTED_SOURCE_PLR_COUNT:
        raise ValueError(
            "Accepted + rejected population rows "
            "must reconstruct the 542-row source; "
            f"got {total_rows}"
        )

    return {
        "source_row_count": int(total_rows),
        "accepted_row_count": int(len(accepted)),
        "rejected_row_count": int(len(rejected)),
        "rejection_reasons": {
            str(reason): int(count)
            for reason, count in rejected[
                "rejection_reason"
            ].value_counts().items()
        },
    }


def read_and_validate_population_quality_artifacts(
    accepted_path: Path = ACCEPTED_POPULATION_PATH,
    rejected_path: Path = REJECTED_POPULATION_PATH,
):
    if not accepted_path.exists():
        raise FileNotFoundError(
            "Accepted population artifact "
            f"does not exist: {accepted_path}"
        )
    if not rejected_path.exists():
        raise FileNotFoundError(
            "Rejected population artifact "
            f"does not exist: {rejected_path}"
        )

    accepted = pd.read_parquet(accepted_path)
    rejected = pd.read_parquet(rejected_path)
    metadata = (
        validate_population_quality_artifacts(
            accepted,
            rejected,
        )
    )
    return accepted, rejected, metadata
