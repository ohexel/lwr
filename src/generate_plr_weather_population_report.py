import argparse
from pathlib import Path

import dagster as dg
import pandas as pd
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src.dagster_pipeline.partitions import forecast_key_from_partition
from src.forecast_key import ProjectPaths


TEMPLATE_PATH = Path(
    "reports/templates/plr_weather_population_report.md.j2"
)
OUTPUT_DIR = Path("reports/generated")


def _format_number(value, digits=1):
    if pd.isna(value):
        return "NA"
    return f"{float(value):.{digits}f}"


def _format_integer(value):
    if pd.isna(value):
        return "NA"
    return f"{int(value):,}"


def _build_context(frame, source_path):
    run_times = pd.to_datetime(
        frame["run_time_utc"],
        utc=True,
    ).drop_duplicates()
    valid_times = pd.to_datetime(
        frame["valid_time_utc"],
        utc=True,
    ).drop_duplicates()
    lead_times = (
        frame["lead_time"]
        .astype("string")
        .drop_duplicates()
    )

    if (
        len(run_times) != 1
        or len(valid_times) != 1
        or len(lead_times) != 1
    ):
        raise ValueError(
            "Final dataset must contain exactly one forecast identity"
        )

    status_counts = (
        frame["population_status"]
        .value_counts(dropna=False)
        .to_dict()
    )

    rejected = frame.loc[
        frame["population_status"] != "available"
    ].copy()

    rejection_reason_counts = list(
        rejected["population_rejection_reason"]
        .fillna("unspecified")
        .value_counts()
        .sort_index()
        .items()
    )

    hottest_rows = []
    hottest = (
        frame.sort_values("temperature_c", ascending=False)
        .head(10)
    )

    for rank, (_, row) in enumerate(
        hottest.iterrows(),
        start=1,
    ):
        hottest_rows.append(
            {
                "rank": rank,
                "plr_id": str(row["plr_id"]),
                "temperature_c": _format_number(
                    row["temperature_c"], 1
                ),
                "relative_humidity_percent": _format_number(
                    row["relative_humidity_percent"], 1
                ),
                "population_total": _format_integer(
                    row["population_total"]
                ),
                "population_65plus": _format_integer(
                    row["population_65plus"]
                ),
                "share_65plus": (
                    "NA"
                    if pd.isna(row["share_65plus"])
                    else f"{100 * float(row['share_65plus']):.1f}%"
                ),
                "population_status": str(
                    row["population_status"]
                ),
            }
        )

    exceptions = [
        {
            "plr_id": str(row["plr_id"]),
            "population_status": str(
                row["population_status"]
            ),
            "population_rejection_reason": str(
                row["population_rejection_reason"]
            ),
        }
        for _, row in rejected.iterrows()
    ]

    return {
        "source_path": str(source_path),
        "row_count": int(len(frame)),
        "unique_plr_count": int(
            frame["plr_id"].nunique()
        ),
        "available_count": int(
            status_counts.get("available", 0)
        ),
        "rejected_count": int(
            status_counts.get(
                "rejected_source_record", 0
            )
        ),
        "run_time_utc": (
            run_times.iloc[0]
            .strftime("%Y-%m-%d %H:%M")
        ),
        "valid_time_utc": (
            valid_times.iloc[0]
            .strftime("%Y-%m-%d %H:%M")
        ),
        "lead_time": str(lead_times.iloc[0]),
        "rejection_reason_counts": (
            rejection_reason_counts
        ),
        "hottest": hottest_rows,
        "population_exceptions": exceptions,
    }


def generate_report(partition_key):
    lead_time, run_time = partition_key.split("|", 1)
    key = dg.MultiPartitionKey(
        {
            "lead_time": lead_time,
            "run_time": run_time,
        }
    )
    forecast = forecast_key_from_partition(key)
    source_path = (
        ProjectPaths()
        .analytical_plr_weather_population(
            forecast=forecast
        )
    )

    frame = pd.read_parquet(source_path)
    context = _build_context(
        frame,
        source_path,
    )

    env = Environment(
        loader=FileSystemLoader(
            str(TEMPLATE_PATH.parent)
        ),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    rendered = env.get_template(
        TEMPLATE_PATH.name
    ).render(**context)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_path = OUTPUT_DIR / (
        "plr_weather_population_"
        f"{run_time}_{lead_time}.md"
    )
    output_path.write_text(
        rendered,
        encoding="utf-8",
    )
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--partition",
        required=True,
        help=(
            "For example "
            "PT000H00M|20260819T1700"
        ),
    )
    args = parser.parse_args()
    print(
        generate_report(
            args.partition
        )
    )


if __name__ == "__main__":
    main()
