"""Build the static Germany inputs once: VG250 and Zensus 2022 raw loads,
the normalized hierarchy and population, the Germany grid subset and the
cell-Gemeinde bridge, with checks 1-4.

Usage:
    uv run --env-file .env python -m src.germany.run_static

Needs ZENSUS2022_TOKEN only when the Zensus files are not yet present under
data/raw/germany/. Safe to repeat: an existing geography version is left
unchanged; population and bridge are rebuilt.
"""

import json
import logging
from pathlib import Path
from time import perf_counter

from src.bootstrap import ensure_dagster_home
from src.germany.run_forecast import execute_job


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_germany_static(project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    ensure_dagster_home(project_root)
    started = perf_counter()
    result = execute_job("germany_static")
    if not result.success:
        raise RuntimeError(
            f"germany_static failed; inspect Dagster run {result.run_id}"
        )
    return {
        "status": "ready",
        "wall_seconds": round(perf_counter() - started, 1),
        "checks": {
            evaluation.check_name: {
                "passed": evaluation.passed,
                "severity": evaluation.severity.value,
            }
            for evaluation in result.get_asset_check_evaluations()
        },
    }


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
    )
    print(json.dumps(run_germany_static(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
