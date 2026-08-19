import requests
import dagster as dg

from src.dagster_pipeline.assets.icon_d2_ruc import (
    ALL_ICON_D2_RUC_ASSETS,
)
from src.dagster_pipeline.partitions import (
    WEATHER_LEAD_TIMES,
    WEATHER_PARTITION_START,
    weather_partition_key,
)
from src.dwd_icon_d2_ruc import (
    advertised_run_times,
    make_session,
)
from src.dwd_weather_availability import (
    find_ready_weather_forecast,
)


RUN_DISCOVERY_INDICATOR = "T_2M"
SENSOR_MAX_RUN_TIMES = 6
SENSOR_MINIMUM_INTERVAL_SECONDS = 60


ICON_D2_RUC_WEATHER_JOB = dg.define_asset_job(
    name="icon_d2_ruc_weather_ingestion",
    selection=ALL_ICON_D2_RUC_ASSETS,
    description=(
        "Materialize the five raw and five normalized "
        "ICON D2 RUC weather assets for one forecast partition."
    ),
)


def weather_sensor_run_key(forecast) -> str:
    """
    Stable idempotency key for one forecast partition.

    Dagster uses sensor run keys to prevent duplicate runs for repeated
    sensor evaluations that return the same partition.
    """
    return (
        "dwd_icon_d2_ruc:"
        f"{forecast.run_label}:"
        f"{forecast.lead_time_label}"
    )


def _skip_reason_for_decision(
    decision,
) -> dg.SkipReason:
    if decision.latest_incomplete is not None:
        availability = (
            decision.latest_incomplete
        )
        forecast = availability.forecast
        missing = ", ".join(
            availability.missing_indicators
        )

        return dg.SkipReason(
            "No complete pending DWD weather "
            "partition is ready. "
            f"Latest incomplete candidate: "
            f"{forecast.run_label} × "
            f"{forecast.lead_time_label}; "
            f"missing: {missing}."
        )

    if (
        decision.already_normalized_forecasts
        > 0
    ):
        return dg.SkipReason(
            "No pending weather partition found "
            "in the recent DWD run window; "
            "checked candidates are already "
            "fully normalized."
        )

    return dg.SkipReason(
        "No eligible DWD weather forecast "
        "candidate was found."
    )


@dg.sensor(
    job=ICON_D2_RUC_WEATHER_JOB,
    minimum_interval_seconds=(
        SENSOR_MINIMUM_INTERVAL_SECONDS
    ),
    default_status=dg.DefaultSensorStatus.STOPPED,
    description=(
        "Poll DWD ICON D2 RUC and launch one weather "
        "partition only after all five required fields "
        "are available for the same run time and lead time."
    ),
)
def dwd_icon_d2_ruc_availability_sensor(context):
    try:
        with make_session() as session:
            run_times = advertised_run_times(
                session,
                RUN_DISCOVERY_INDICATOR,
            )

            if not run_times:
                return dg.SkipReason(
                    "DWD advertised no "
                    f"{RUN_DISCOVERY_INDICATOR} "
                    "forecast runs."
                )

            decision = (
                find_ready_weather_forecast(
                    session,
                    advertised_run_times=run_times,
                    lead_time_labels=(
                        WEATHER_LEAD_TIMES
                    ),
                    minimum_run_time=(
                        WEATHER_PARTITION_START
                    ),
                    max_run_times=(
                        SENSOR_MAX_RUN_TIMES
                    ),
                )
            )

    except requests.RequestException as exc:
        context.log.warning(
            "DWD availability check failed: %s",
            exc,
        )
        return dg.SkipReason(
            "Could not read DWD availability "
            f"because of a network/HTTP error: {exc}"
        )

    if decision.ready is None:
        return _skip_reason_for_decision(
            decision
        )

    forecast = decision.ready.forecast
    partition_key = weather_partition_key(
        forecast
    )
    run_key = weather_sensor_run_key(
        forecast
    )

    context.log.info(
        "Complete DWD weather partition ready: "
        "%s × %s",
        forecast.run_label,
        forecast.lead_time_label,
    )

    return dg.RunRequest(
        run_key=run_key,
        partition_key=partition_key,
        tags={
            "source": "dwd_icon_d2_ruc",
            "forecast_run_time": (
                forecast.run_label
            ),
            "forecast_lead_time": (
                forecast.lead_time_label
            ),
        },
    )
