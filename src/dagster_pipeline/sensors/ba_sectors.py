"""Load each new quarter of the BA sector data when it is published.

The BA publishes both products about six months after the quarter's end
(31 Mar 2026 on 10/15 Oct 2026; 30 Jun 2026 announced for January 2027).
Once a day the sensor sends two HEAD requests for the quarter after the
newest one loaded (the Regionalreport of one Kreis and the Germany WZ
booklet), unless a rate-limit cool-down is active; when both exist it
requests the germany_sectors job, which then downloads the other files one
at a time. Off by default: it polls an external host.
"""

from datetime import date

import dagster as dg
import psycopg
import requests

from src.database.connection import database_connection
from src.dagster_pipeline.germany_jobs import GERMANY_SECTORS_JOB, sector_run_config
from src.germany.ba_sectors import BaRateLimited, PoliteClient, cooldown_until, next_quarter
from src.germany.contract import (
    BA_REGIONALREPORT_CONTRACT,
    BA_WZ_HEFT_CONTRACT,
    SECTOR_LATEST_KNOWN,
)


SENSOR_MINIMUM_INTERVAL_SECONDS = 24 * 60 * 60


def latest_loaded_file_date() -> date | None:
    try:
        with database_connection(application_name="lwr_germany_sector_sensor") as connection:
            return connection.execute(
                "SELECT MAX(file_date) FROM raw.de_ba_regionalreport"
            ).fetchone()[0]
    except psycopg.errors.UndefinedTable:
        return None


def candidate_file_date(loaded: date | None) -> date:
    return SECTOR_LATEST_KNOWN if loaded is None else next_quarter(loaded)


def quarter_is_published(client: PoliteClient, file_date: date) -> bool:
    return client.is_published(
        BA_REGIONALREPORT_CONTRACT.url(BA_REGIONALREPORT_CONTRACT.probe_kreis_key, file_date)
    ) and client.is_published(BA_WZ_HEFT_CONTRACT.url("d", file_date))


@dg.sensor(
    job=GERMANY_SECTORS_JOB,
    minimum_interval_seconds=SENSOR_MINIMUM_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description=(
        "Daily HEAD requests for the next quarter of the BA sector data; "
        "requests germany_sectors once it is published."
    ),
)
def ba_sectors_release_sensor(context: dg.SensorEvaluationContext) -> dg.RunRequest | dg.SkipReason:
    until = cooldown_until()
    if until is not None:
        return dg.SkipReason(f"BA rate-limit cool-down until {until.isoformat(timespec='minutes')}")
    loaded = latest_loaded_file_date()
    candidate = candidate_file_date(loaded)
    try:
        with requests.Session() as session:
            published = quarter_is_published(PoliteClient(session), candidate)
    except BaRateLimited as stop:
        return dg.SkipReason(str(stop))
    if not published:
        return dg.SkipReason(
            f"BA sector data for {candidate.isoformat()} not published yet "
            f"(newest loaded: {loaded.isoformat() if loaded else 'none'})"
        )
    return dg.RunRequest(
        run_key=f"ba_sectors:{candidate.isoformat()}",
        run_config=sector_run_config(candidate),
        tags={"ba_sector_file_date": candidate.isoformat()},
    )
