"""Load one quarter of the BA sector data (Regionalreport per Kreis, WZ
booklets per Land) into the Germany layer, with the sector checks.

Usage:
    uv run --env-file .env python -m src.germany.run_sectors [--file-date 2026-03-31]

Needs the hierarchy from python -m src.germany.run_static first; the check
against the Gemeindedaten uses python -m src.germany.run_employment when it
was run. A first run downloads 434 files one at a time with a 3 s pause (about 22
minutes); files already on disk are not downloaded again. Safe to repeat.

If the BA statistics server answers HTTP 429 (too many requests) and keeps
doing so after waiting, the run stops, records a cool-down in
data/raw/germany/ba_rate_limit.json and exits with status 2 and a message
saying when to run again; the next run continues with the missing files.
"""

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from time import perf_counter

from src.bootstrap import ensure_dagster_home
from src.dagster_pipeline.germany_jobs import sector_run_config
from src.germany.ba_sectors import cooldown_until
from src.germany.contract import BA_REGIONALREPORT_CONTRACT, SECTOR_LATEST_KNOWN
from src.germany.run_forecast import execute_job


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SectorRunRateLimited(RuntimeError):
    """The BA asked us to slow down; the run stopped and can be repeated later."""


def _files_on_disk(file_date: date, project_root: Path) -> int:
    folder = project_root / BA_REGIONALREPORT_CONTRACT.path("00000", file_date).parent
    return len(list(folder.glob("bst-reg-*.xlsx"))) if folder.exists() else 0


def _rate_limit_message(file_date: date, project_root: Path, until) -> str:
    return (
        f"The BA statistics server asked us to slow down (HTTP 429). "
        f"{_files_on_disk(file_date, project_root)} of 400 Kreis files for {file_date} are on disk "
        f"and will not be downloaded again. Run the same command again after "
        f"{until.astimezone().isoformat(timespec='minutes')}."
    )


def run_germany_sectors(file_date: date, project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    ensure_dagster_home(project_root)
    cooldown_path = project_root / "data/raw/germany/ba_rate_limit.json"
    until = cooldown_until(cooldown_path)
    if until is not None:
        raise SectorRunRateLimited(_rate_limit_message(file_date, project_root, until))
    started = perf_counter()
    result = execute_job("germany_sectors", run_config=sector_run_config(file_date))
    if not result.success:
        until = cooldown_until(cooldown_path)
        if until is not None:
            raise SectorRunRateLimited(_rate_limit_message(file_date, project_root, until))
        raise RuntimeError(f"germany_sectors failed; inspect Dagster run {result.run_id}")
    return {
        "status": "ready",
        "file_date": file_date.isoformat(),
        "wall_seconds": round(perf_counter() - started, 1),
        "checks": {
            evaluation.check_name: {"passed": evaluation.passed, "severity": evaluation.severity.value}
            for evaluation in result.get_asset_check_evaluations()
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file-date", type=date.fromisoformat, default=SECTOR_LATEST_KNOWN)
    args = parser.parse_args(argv)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
    try:
        print(json.dumps(run_germany_sectors(args.file_date), indent=2))
    except SectorRunRateLimited as stop:
        print(json.dumps({"status": "rate_limited", "message": str(stop)}, indent=2, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
