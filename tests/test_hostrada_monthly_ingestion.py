from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.hostrada_contract import HostradaMonthKey
from src.hostrada_paths import HostradaPaths, hostrada_source_url
from src.ingestion.hostrada_month import (
    BerlinCellWindow,
    _hour_values,
    _stage_rows,
)


def _window() -> BerlinCellWindow:
    return BerlinCellWindow(
        y_indices=np.asarray([1, 2], dtype=np.int64),
        x_indices=np.asarray([2, 1], dtype=np.int64),
        y_start=1,
        y_stop=3,
        x_start=1,
        x_stop=3,
    )


def _dataset(variable_name: str, values: np.ndarray) -> SimpleNamespace:
    return SimpleNamespace(variables={variable_name: values})


def _datasets(humidity: float = 60.0) -> dict[str, SimpleNamespace]:
    temperature = np.full((1, 3, 3), 25.0)
    relative_humidity = np.full((1, 3, 3), humidity)
    wind = np.full((1, 3, 3), 2.0)
    return {
        "tas": _dataset("tas", temperature),
        "hurs": _dataset("hurs", relative_humidity),
        "sfcWind": _dataset("sfcWind", wind),
    }


def test_hostrada_paths_follow_dwd_directory_and_filename_contract():
    month = HostradaMonthKey(2026, 6)
    paths = HostradaPaths(project_root=Path("/project"))

    assert paths.source_file(month, "hurs") == Path(
        "/project/data/raw/hostrada/humidity_relative/"
        "hurs_1hr_HOSTRADA-v1-0_BE_gn_2026060100-2026063023.nc"
    )
    assert hostrada_source_url(month, "sfcWind").endswith(
        "/wind_speed/"
        "sfcWind_1hr_HOSTRADA-v1-0_BE_gn_2026060100-2026063023.nc"
    )


def test_hostrada_reads_paired_berlin_cell_coordinates():
    values = np.arange(9, dtype=np.float64).reshape(1, 3, 3)

    observed = _hour_values(_dataset("tas", values), "tas", 0, _window())

    assert observed.tolist() == [5.0, 7.0]


def test_hostrada_rejects_missing_or_invalid_berlin_values():
    missing = _datasets()
    masked_temperature = np.ma.array(
        missing["tas"].variables["tas"],
        mask=False,
    )
    masked_temperature.mask[0, 1, 2] = True
    missing["tas"].variables["tas"] = masked_temperature

    with pytest.raises(ValueError, match="missing Berlin values"):
        next(_stage_rows(missing, HostradaMonthKey(2026, 6), _window()))

    with pytest.raises(ValueError, match="humidity is outside"):
        next(
            _stage_rows(
                _datasets(humidity=101.0),
                HostradaMonthKey(2026, 6),
                _window(),
            )
        )
