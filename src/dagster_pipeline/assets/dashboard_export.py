"""Export and atomically publish the static dashboard for the current horizon."""

import os
from pathlib import Path
import shutil

import dagster as dg
from dagster import AssetExecutionContext

from src.dagster_pipeline.assets.database_analytical import (
    ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
)
from src.dagster_pipeline.assets.historical_trajectories import (
    HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY,
)
from src.export_temperature_dashboard import export_temperature_dashboard


STATIC_DASHBOARD_EXPORT_KEY = dg.AssetKey(["analytical", "static_dashboard_export"])
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STAGING_ROOT = PROJECT_ROOT / "web" / ".dashboard-staging"
PUBLISH_LINK = PROJECT_ROOT / "web" / "temperature-dashboard-current"


@dg.asset(
    key=STATIC_DASHBOARD_EXPORT_KEY,
    deps=[
        ANALYTICAL_PLR_WEATHER_POPULATION_KEY,
        HISTORICAL_TEMPERATURE_TRAJECTORIES_KEY,
    ],
    group_name="analytical",
    description=(
        "Exports the current complete 25-hour forecast horizon, including "
        "its matching 31-year historical trajectories, to a self-contained "
        "static directory and atomically publishes it. Materialized once "
        "per complete horizon by dashboard_export_sensor, after "
        "historical_temperature_trajectories; never on a wall-clock "
        "schedule."
    ),
)
def static_dashboard_export(context: AssetExecutionContext) -> None:
    staging_dir = STAGING_ROOT / context.run_id
    try:
        result = export_temperature_dashboard(output_dir=staging_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    _publish_atomically(staging_dir)
    _prune_staging(keep=staging_dir)

    context.add_output_metadata(
        {
            "run_time_berlin": str(result["run_time_berlin"]),
            "plr_count": result["plr_count"],
            "lead_hour_count": result["lead_hour_count"],
            "historical_year_count": result["historical_year_count"],
            "detail_file_count": result["detail_file_count"],
            "published_path": str(PUBLISH_LINK),
        }
    )


def _publish_atomically(staging_dir: Path) -> None:
    """Blue/green symlink swap; the live directory is never edited in place.

    The target is written as a path relative to PUBLISH_LINK's own directory
    (web/), not as staging_dir's absolute path. The container sees this
    repository at /app while a host bind-mounting the same volume sees it at
    a different absolute path; an absolute symlink target baked in from
    inside the container resolves only inside the container's own mount
    namespace and appears broken everywhere else. The relative structure
    under web/ is identical on both sides of the bind mount.
    """
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    new_link = PUBLISH_LINK.with_name(f".{PUBLISH_LINK.name}.new")
    if new_link.exists() or new_link.is_symlink():
        new_link.unlink()
    relative_target = Path(STAGING_ROOT.name) / staging_dir.name
    new_link.symlink_to(relative_target, target_is_directory=True)
    os.replace(new_link, PUBLISH_LINK)  # atomic on the same filesystem


def _prune_staging(*, keep: Path, keep_previous: int = 1) -> None:
    """Keep the just-published export plus one prior version for rollback."""
    directories = sorted(
        (path for path in STAGING_ROOT.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in directories[1 + keep_previous :]:
        if stale != keep:
            shutil.rmtree(stale, ignore_errors=True)


DASHBOARD_EXPORT_ASSETS = [static_dashboard_export]
