"""Load one quarter of the BA sector data (Regionalreport per Kreis, WZ
booklets per Land) into the Germany layer, with the sector checks.

Usage:
    uv run --env-file .env python -m src.germany.run_sectors [--file-date 2026-03-31]

Needs the hierarchy from python -m src.germany.run_static first; the check
against the Gemeindedaten uses python -m src.germany.run_employment when it
was run. A first run downloads 434 files one at a time (about 15 minutes);
files already on disk are not downloaded again. Safe to repeat.
"""

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from time import perf_counter

from src.bootstrap import ensure_dagster_home
from src.dagster_pipeline.germany_jobs import sector_run_config
from src.germany.contract import SECTOR_LATEST_KNOWN
from src.germany.run_forecast import execute_job


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_germany_sectors(file_date: date, project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    ensure_dagster_home(project_root)
    started = perf_counter()
    result = execute_job("germany_sectors", run_config=sector_run_config(file_date))
    if not result.success:
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
    print(json.dumps(run_germany_sectors(args.file_date), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
