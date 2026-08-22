import requests
import dagster as dg

from src.dagster_pipeline.partitions import (
    WEATHER_LEAD_TIMES,
    WEATHER_HISTORY_START,
    weather_partition_key,
)
from src.dwd_icon_d2_ruc import (
    advertised_run_times,
    make_session,
)
from src.dwd_weather_availability import (
    find_ready_weather_forecasts,
    dwd_polling_window_open
)
from src.dagster_pipeline.assets.database_weather_raw import (
    ICON_D2_RUC_DATABASE_ACQUISITION_JOB,
)
from src.database.weather_state import (
    raw_weather_partition_loaded,
)


RUN_DISCOVERY_INDICATOR = "T_2M"
# we want to check if the N most recent ICON D2 RUC runs are present locally
SENSOR_MAX_RUN_TIMES = 3
# at which intervals should the sensor ping the DWD API
SENSOR_MINIMUM_INTERVAL_SECONDS = 300
# if a data retrieval fails, how often do we retry
SENSOR_MAX_ATTEMPTS = 5


def weather_sensor_run_key(forecast, attempt: int) -> str:
    """
    Stable idempotency key for one forecast partition.

    Dagster uses sensor run keys to prevent duplicate runs for repeated
    sensor evaluations that return the same partition.

    But we also want to account for the fact that a prior run for the same
    lead time might have failed.
    """
    return (
        "dwd_icon_d2_ruc:"
        f"{forecast.run_label}:"
        f"{forecast.lead_time_label}:"
        f"attempt_{attempt}"
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
        decision.already_complete_forecasts
        > 0
    ):
        return dg.SkipReason(
            "No pending weather partition found "
            "in the recent DWD run window; "
            "checked candidates are already "
            "complete in raw PostgreSQL storage."
        )

    return dg.SkipReason(
        "No eligible DWD weather forecast "
        "candidate was found."
    )


@dg.sensor(
    job=ICON_D2_RUC_DATABASE_ACQUISITION_JOB,
    minimum_interval_seconds=(
        SENSOR_MINIMUM_INTERVAL_SECONDS
    ),
    default_status=dg.DefaultSensorStatus.STOPPED,
    description=(
        "Poll DWD ICON D2 RUC and launch one or more weather "
        "partition(s) only after all five required fields "
        "are available for the same run time and lead time."
    ),
)
def dwd_icon_d2_ruc_availability_sensor(context):
    if not dwd_polling_window_open():
        return dg.SkipReason(
                "Outside DWD polling window (:30-:59 each hour)"
                )
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
                find_ready_weather_forecasts(
                    session,
                    advertised_run_times = run_times,
                    already_complete_fn=raw_weather_partition_loaded,
                    lead_time_labels=(
                        WEATHER_LEAD_TIMES
                    ),
                    minimum_run_time=(
                        WEATHER_HISTORY_START
                    ),
                    max_run_times=(
                        SENSOR_MAX_RUN_TIMES
                    ),
                )
            )

            context.log.info(
                    "Availability helper found ready forecasts: %s",
                    [(item.forecast.run_label, item.forecast.lead_time_label) for item in decision.ready]
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

    if not decision.ready:
        return _skip_reason_for_decision(decision)

    run_requests = []

    for availability in decision.ready:
        forecast = availability.forecast

        runs = context.instance.get_runs(
                filters = dg.RunsFilter(
                    tags = {
                        "forecast_run_time": forecast.run_label,
                        "forecast_lead_time": forecast.lead_time_label
                        }
                    )
            )

        attempt = len(runs) + 1
        latest_run = runs[0] if runs else None

        context.log.info(
                "Forecast %s x %s has %d prior Dagster runs; latest status = %s",
                forecast.run_label,
                forecast.lead_time_label,
                len(runs),
                latest_run.status.value if latest_run else "NONE"
                )

        if latest_run is None:
            pass
        elif latest_run.status in {
                dg.DagsterRunStatus.NOT_STARTED,
                dg.DagsterRunStatus.QUEUED,
                dg.DagsterRunStatus.STARTING,
                dg.DagsterRunStatus.STARTED,
                dg.DagsterRunStatus.CANCELING,
                dg.DagsterRunStatus.SUCCESS
                }:
            continue
        elif latest_run.status in {
                dg.DagsterRunStatus.FAILURE,
                dg.DagsterRunStatus.CANCELED
                }:
            if attempt > SENSOR_MAX_ATTEMPTS:
                continue

        context.log.info(
            "Complete DWD weather partition ready: "
            "%s × %s",
            forecast.run_label,
            forecast.lead_time_label,
        )

        run_key = weather_sensor_run_key(forecast, attempt)
        partition_key = weather_partition_key(forecast)

        run_requests.append(
            dg.RunRequest(
                run_key = run_key,
                partition_key = partition_key,
                tags={
                    "source": "dwd_icon_d2_ruc",
                    "forecast_run_time": forecast.run_label,
                    "forecast_lead_time": forecast.lead_time_label,
                    "attempt": str(attempt)
                },
            )
        )

    context.log.info(
                "Submitting %d weather run requests: %s",
                len(run_requests),
                [ request.partition_key for request in run_requests ]
                )
    
    return dg.SensorResult( run_requests = run_requests )
