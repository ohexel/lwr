from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from src.database.weather_mask import WeatherMaskState, current_weather_mask
from src.forecast_key import ForecastKey
from src.ingestion import icon_d2_ruc_field
from src.ingestion.icon_d2_ruc_field import _masked_stage_rows


def test_copy_rows_include_only_requested_berlin_mask_cells() -> None:
    values = np.array([280.0, 281.0, 282.0, np.nan, 284.0])

    rows = list(_masked_stage_rows(values, (1, 3, 4)))

    assert rows == [
        (1, 281.0),
        (3, None),
        (4, 284.0),
    ]


def test_copy_rows_reject_an_out_of_range_mask_index() -> None:
    values = np.array([280.0, 281.0])

    with pytest.raises(ValueError, match="outside the decoded field"):
        list(_masked_stage_rows(values, (0, 2)))


@pytest.mark.parametrize(
    "database_rows, message",
    [
        ([(2,), (2,)], "duplicates"),
        ([(542_040,)], "out-of-range"),
    ],
)
def test_weather_mask_rejects_indices_that_cannot_safely_select_source_values(
    database_rows,
    message,
) -> None:
    connection = Mock()
    geography_cursor = Mock()
    geography_cursor.fetchone.return_value = ("2023-01-01",)
    mask_cursor = Mock()
    mask_cursor.fetchall.return_value = database_rows
    connection.execute.side_effect = [geography_cursor, mask_cursor]

    with pytest.raises(RuntimeError, match=message):
        current_weather_mask(
            connection,
            source_grid_id="icon_grid_0047_R19B07_L",
            mask_buffer_m=5000,
        )


def test_forecast_loader_copies_only_mask_cells_but_accounts_for_full_source(
    tmp_path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "field.grib2"
    source_path.write_bytes(b"fixture")
    connection = Mock()
    connection.execute.return_value.rowcount = 2
    connection.execute.return_value.fetchone.return_value = (1,)
    copied_rows = []

    @contextmanager
    def fake_database_connection(**kwargs):
        yield connection

    def fake_prepare_field(*, paths, forecast, indicator):
        return icon_d2_ruc_field.PreparedField(
            indicator=indicator,
            source_path=Path(source_path),
            source_url="https://example.invalid/field.grib2",
            source_sha256="fixture-sha256",
            source_unit="fixture-unit",
            values=np.array([280.0, 281.0, 282.0, np.nan, 284.0]),
        )

    def fake_copy_rows(connection, *, schema, table, columns, rows):
        observed = list(rows)
        copied_rows.append(observed)
        return SimpleNamespace(row_count=len(observed))

    monkeypatch.setattr(
        icon_d2_ruc_field,
        "ICON_D2_GRID_CONTRACT",
        SimpleNamespace(field_point_count=5, source_grid_id="fixture-grid"),
    )
    monkeypatch.setattr(icon_d2_ruc_field, "database_connection", fake_database_connection)
    monkeypatch.setattr(icon_d2_ruc_field, "_source_path", lambda *args, **kwargs: source_path)
    monkeypatch.setattr(icon_d2_ruc_field, "_prepare_field", fake_prepare_field)
    monkeypatch.setattr(icon_d2_ruc_field, "copy_rows", fake_copy_rows)
    monkeypatch.setattr(
        icon_d2_ruc_field,
        "current_weather_mask",
        lambda connection, **kwargs: WeatherMaskState(
            geography_version="2023-01-01",
            source_grid_id="fixture-grid",
            mask_buffer_m=5000,
            mask_cell_count=2,
            cell_indices=(1, 3),
        ),
    )
    monkeypatch.setattr(
        icon_d2_ruc_field,
        "query_raw_weather_partition_state",
        lambda connection, forecast: SimpleNamespace(passed=True, mask_cell_count=2),
    )

    result = icon_d2_ruc_field.load_icon_d2_ruc_raw_partition(
        ForecastKey.from_dwd_labels(
            run_time="2026-08-25T06:00",
            lead_time="PT000H00M",
        )
    )

    assert copied_rows == [[(1, 281.0), (3, None)]] * 4
    assert result.source_row_count == 20
    assert result.retained_row_count == 8
    assert result.source_missing_value_count == 4
    assert result.retained_missing_value_count == 4
