import dagster as dg
import pandas as pd
from src.build_plr_population_65plus import REJECTED_POPULATION_PATH
from src.dagster_pipeline.assets.plr_weather_population import plr_weather_population
from src.dagster_pipeline.partitions import forecast_key_from_partition
from src.forecast_key import ProjectPaths

EXPECTED_PLR_COUNT = 542
POPULATION_COLUMNS = [
    "population_total",
    "population_65plus",
    "share_65plus",
]

def _read_final_partition(context):
    partition_key = context.partition_key
    if not isinstance(partition_key, dg.MultiPartitionKey):
        raise ValueError("Final asset checks require run_time × lead_time")
    forecast = forecast_key_from_partition(partition_key)
    path = ProjectPaths().analytical_plr_weather_population(
        forecast=forecast
    )
    if not path.exists():
        raise FileNotFoundError(
            f"Final analytical partition does not exist: {path}"
        )
    return pd.read_parquet(path), forecast

@dg.asset_check(
    asset=plr_weather_population,
    name="complete_plr_coverage",
)
def complete_plr_coverage(context: dg.AssetCheckExecutionContext):
    frame, _ = _read_final_partition(context)
    row_count = len(frame)
    unique_count = frame["plr_id"].nunique(dropna=True)
    missing_ids = int(frame["plr_id"].isna().sum())
    passed = (
        row_count == EXPECTED_PLR_COUNT
        and unique_count == EXPECTED_PLR_COUNT
        and missing_ids == 0
    )
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "row_count": row_count,
            "unique_plr_count": unique_count,
            "missing_plr_ids": missing_ids,
        },
    )

@dg.asset_check(
    asset=plr_weather_population,
    name="forecast_identity_matches_partition",
)
def forecast_identity_matches_partition(
    context: dg.AssetCheckExecutionContext,
):
    frame, forecast = _read_final_partition(context)
    run_times = pd.to_datetime(
        frame["run_time_utc"], utc=True
    ).drop_duplicates()
    valid_times = pd.to_datetime(
        frame["valid_time_utc"], utc=True
    ).drop_duplicates()
    lead_times = frame["lead_time"].astype("string").drop_duplicates()

    actual_run = run_times.iloc[0] if len(run_times) == 1 else None
    actual_valid = valid_times.iloc[0] if len(valid_times) == 1 else None
    actual_lead = str(lead_times.iloc[0]) if len(lead_times) == 1 else None
    expected_run = pd.Timestamp(forecast.run_time)
    expected_valid = pd.Timestamp(forecast.valid_time)

    passed = (
        len(run_times) == 1
        and len(valid_times) == 1
        and len(lead_times) == 1
        and actual_run == expected_run
        and actual_valid == expected_valid
        and actual_lead == forecast.lead_time_label
    )
    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "actual_run_time": str(actual_run),
            "expected_run_time": str(expected_run),
            "actual_valid_time": str(actual_valid),
            "expected_valid_time": str(expected_valid),
            "actual_lead_time": str(actual_lead),
            "expected_lead_time": forecast.lead_time_label,
        },
    )

@dg.asset_check(
    asset=plr_weather_population,
    name="population_exceptions_explained",
)
def population_exceptions_explained(
    context: dg.AssetCheckExecutionContext,
):
    frame, _ = _read_final_partition(context)
    rejected = pd.read_parquet(REJECTED_POPULATION_PATH)

    final_rejected_ids = set(
        frame.loc[
            frame["population_status"] == "rejected_source_record",
            "plr_id",
        ].astype(str)
    )
    registry_ids = set(rejected["plr_id"].astype(str))
    actual_statuses = set(
        frame["population_status"].dropna().astype(str)
    )
    allowed_statuses = {"available", "rejected_source_record"}

    available = frame.loc[
        frame["population_status"] == "available"
    ]
    final_rejected = frame.loc[
        frame["population_status"] == "rejected_source_record"
    ]

    available_complete = not available[POPULATION_COLUMNS].isna().any().any()
    rejected_metrics_missing = final_rejected[POPULATION_COLUMNS].isna().all(axis=None)
    rejected_reasons_present = not final_rejected["population_rejection_reason"].isna().any()
    # Recast as native booleans because Dagster does not recognize numpy booleans in metadata
    available_complete = bool(available_complete)
    rejected_metrics_missing = bool(rejected_metrics_missing)
    rejected_reasons_present = bool(rejected_metrics_missing)

    passed = (
        actual_statuses <= allowed_statuses
        and not frame["population_status"].isna().any()
        and final_rejected_ids == registry_ids
        and available_complete
        and rejected_metrics_missing
        and rejected_reasons_present
    )

    return dg.AssetCheckResult(
        passed=passed,
        metadata={
            "available_count": int(len(available)),
            "final_rejected_count": int(len(final_rejected)),
            "registry_rejected_count": int(len(rejected)),
            "available_complete": available_complete,
            "rejected_metrics_missing": rejected_metrics_missing,
            "rejected_reasons_present": rejected_reasons_present,
        },
    )

FINAL_ANALYTICAL_ASSET_CHECKS = [
    complete_plr_coverage,
    forecast_identity_matches_partition,
    population_exceptions_explained,
]
