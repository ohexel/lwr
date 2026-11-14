"""Load each new yearly vintage of the BA Gemeindedaten when it is published.

The BA publishes the file for 30 June of a year in January of the next year
(the 2025 file on 15 Jan 2026; the imprint announces the next for
15 Jan 2027). Once a day the sensor sends one HEAD request for the vintage
after the newest one loaded; when the file exists it requests the
germany_employment job for that reference date. The checks then compare the
new vintage with the source totals and list its territorial changes for
review (see ADR 019). Off by default, like the DWD sensor: it polls an
external host.
"""

from datetime import date

import dagster as dg
import psycopg
import requests

from src.database.connection import database_connection
from src.dagster_pipeline.germany_jobs import GERMANY_EMPLOYMENT_JOB, employment_run_config
from src.germany.ba_gemeindedaten import next_reference_date, vintage_is_published
from src.germany.contract import BA_GEMEINDEDATEN_CONTRACT
from src.germany.indicators import latest_loaded_reference_date


SENSOR_MINIMUM_INTERVAL_SECONDS = 24 * 60 * 60


def candidate_reference_date(loaded: date | None) -> date:
    """The vintage to look for: the contract's newest one when nothing is
    loaded yet, otherwise the year after the newest loaded one."""
    if loaded is None:
        return BA_GEMEINDEDATEN_CONTRACT.latest_known
    return next_reference_date(loaded)


def ba_run_key(reference_date: date) -> str:
    return f"ba_gemeindedaten:{reference_date.isoformat()}"


@dg.sensor(
    job=GERMANY_EMPLOYMENT_JOB,
    minimum_interval_seconds=SENSOR_MINIMUM_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description=(
        "Daily HEAD request for the next yearly BA Gemeindedaten file; "
        "requests germany_employment for it once it is published."
    ),
)
def ba_gemeindedaten_release_sensor(
    context: dg.SensorEvaluationContext,
) -> dg.RunRequest | dg.SkipReason:
    try:
        with database_connection(application_name="lwr_germany_ba_sensor") as connection:
            loaded = latest_loaded_reference_date(connection)
    except psycopg.errors.UndefinedTable:
        loaded = None
    candidate = candidate_reference_date(loaded)
    with requests.Session() as session:
        published = vintage_is_published(session, candidate)
    if not published:
        return dg.SkipReason(
            f"BA Gemeindedaten {candidate.isoformat()} not published yet "
            f"(newest loaded: {loaded.isoformat() if loaded else 'none'})"
        )
    return dg.RunRequest(
        run_key=ba_run_key(candidate),
        run_config=employment_run_config(candidate),
        tags={"ba_reference_date": candidate.isoformat()},
    )
