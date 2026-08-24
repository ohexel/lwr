from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.hostrada_contract import (
    HOSTRADA_FIELD_CONTRACTS,
    HOSTRADA_GRID_CONTRACT,
    HOSTRADA_REQUIRED_VARIABLES,
    HOSTRADA_TIME_CALENDAR,
    HOSTRADA_TIME_EPOCH,
    HOSTRADA_TIME_UNITS,
    HostradaMonthKey,
    get_hostrada_field,
    hostrada_grid_fingerprint,
    validate_hostrada_month,
)


class _FakeVariable:
    def __init__(
        self,
        values=None,
        *,
        dimensions=(),
        shape=None,
        dtype=None,
        **attributes,
    ):
        self.values = None if values is None else np.asarray(values)
        self.dimensions = dimensions
        self.shape = (
            tuple(shape)
            if shape is not None
            else (() if self.values is None else self.values.shape)
        )
        self.dtype = np.dtype(
            dtype if dtype is not None else (
                "float64" if self.values is None else self.values.dtype
            )
        )
        for name, value in attributes.items():
            setattr(self, name, value)

    def __getitem__(self, key):
        return self.values[key]


class _FakeDataset:
    def __init__(self, variables):
        self.variables = variables


def _month_datasets(month: HostradaMonthKey) -> dict[str, _FakeDataset]:
    datasets = {}
    start_days = (month.start_utc - HOSTRADA_TIME_EPOCH) / timedelta(days=1)

    for field in HOSTRADA_FIELD_CONTRACTS:
        datasets[field.variable_name] = _FakeDataset(
            {
                "X": _FakeVariable(
                    HOSTRADA_GRID_CONTRACT.x_coordinates.astype("float32"),
                    dimensions=("X",),
                ),
                "Y": _FakeVariable(
                    HOSTRADA_GRID_CONTRACT.y_coordinates.astype("float32"),
                    dimensions=("Y",),
                ),
                "time": _FakeVariable(
                    float(start_days)
                    + np.arange(month.hour_count, dtype="float64") / 24,
                    dimensions=("time",),
                    units=HOSTRADA_TIME_UNITS,
                    calendar=HOSTRADA_TIME_CALENDAR,
                ),
                "crs": _FakeVariable(
                    epsg_code="EPSG:3034",
                    grid_mapping_name="lambert_conformal_conic",
                    standard_parallel=np.asarray([35.0, 65.0]),
                ),
                field.variable_name: _FakeVariable(
                    dimensions=("time", "Y", "X"),
                    shape=(
                        month.hour_count,
                        HOSTRADA_GRID_CONTRACT.y_count,
                        HOSTRADA_GRID_CONTRACT.x_count,
                    ),
                    dtype="int32",
                    units=field.units,
                    scale_factor=field.scale_factor,
                    _FillValue=field.fill_value,
                    grid_mapping="crs",
                ),
            }
        )

    return datasets


def test_hostrada_source_contract_contains_only_required_variables():
    assert HOSTRADA_REQUIRED_VARIABLES == ("tas", "hurs", "sfcWind")
    assert get_hostrada_field("tas").source_directory == "air_temperature_mean"
    assert get_hostrada_field("hurs").source_directory == "humidity_relative"
    assert get_hostrada_field("sfcWind").source_directory == "wind_speed"


def test_hostrada_grid_contract_matches_inspected_coordinates():
    contract = HOSTRADA_GRID_CONTRACT

    assert contract.x_coordinates[0] == 3_670_500.0
    assert contract.x_coordinates[-1] == 4_389_500.0
    assert contract.y_coordinates[0] == 2_242_500.0
    assert contract.y_coordinates[-1] == 3_179_500.0
    assert contract.x_count == 720
    assert contract.y_count == 938
    assert contract.source_srid == 3034
    assert contract.target_srid == 25833
    assert len(contract.grid_fingerprint) == 64
    assert contract.source_grid_id.startswith("hostrada_v1_0_")


def test_hostrada_grid_fingerprint_changes_when_coordinates_change():
    coordinates = HOSTRADA_GRID_CONTRACT.x_coordinates.copy()
    coordinates[0] += 1.0

    assert hostrada_grid_fingerprint(
        coordinates,
        HOSTRADA_GRID_CONTRACT.y_coordinates,
    ) != HOSTRADA_GRID_CONTRACT.grid_fingerprint


def test_hostrada_grid_fingerprint_is_independent_of_coordinate_dtype():
    assert hostrada_grid_fingerprint(
        HOSTRADA_GRID_CONTRACT.x_coordinates.astype("float32"),
        HOSTRADA_GRID_CONTRACT.y_coordinates.astype("float32"),
    ) == HOSTRADA_GRID_CONTRACT.grid_fingerprint


def test_hostrada_month_key_handles_utc_month_and_leap_year():
    june = HostradaMonthKey.from_partition_key("2026-06")
    leap_february = HostradaMonthKey.from_partition_key("2024-02")
    ordinary_february = HostradaMonthKey.from_partition_key("2025-02")

    assert june.partition_key == "2026-06"
    assert june.start_utc == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert june.end_utc == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert june.hour_count == 720
    assert leap_february.hour_count == 696
    assert ordinary_february.hour_count == 672


def test_hostrada_december_partition_advances_to_following_year():
    december = HostradaMonthKey.from_partition_key("2025-12")

    assert december.end_utc == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert december.hour_count == 744


def test_hostrada_month_key_rejects_noncanonical_values():
    for invalid in (
        "2026-6",
        "2026-00",
        "2026-13",
        "2026-06-01",
        " 2026-06",
    ):
        with pytest.raises(ValueError):
            HostradaMonthKey.from_partition_key(invalid)


def test_hostrada_source_filename_matches_inspected_june_file():
    month = HostradaMonthKey.from_partition_key("2026-06")

    assert month.source_filename("tas") == (
        "tas_1hr_HOSTRADA-v1-0_BE_gn_2026060100-2026063023.nc"
    )


def test_hostrada_source_contract_rejects_unknown_variable():
    with pytest.raises(ValueError, match="Unsupported HOSTRADA variable"):
        get_hostrada_field("precipitation")


def test_hostrada_month_validation_accepts_three_aligned_fields():
    month = HostradaMonthKey.from_partition_key("2026-06")
    summary = validate_hostrada_month(_month_datasets(month), month)

    assert summary.source_grid_id == HOSTRADA_GRID_CONTRACT.source_grid_id
    assert summary.grid_fingerprint == HOSTRADA_GRID_CONTRACT.grid_fingerprint
    assert summary.hour_count == 720
    assert summary.first_utc == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert summary.last_utc == datetime(2026, 6, 30, 23, tzinfo=timezone.utc)
    assert summary.variables == ("tas", "hurs", "sfcWind")


def test_hostrada_month_validation_rejects_missing_field():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    del datasets["hurs"]

    with pytest.raises(ValueError, match="missing"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_unexpected_extra_field():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["unexpected"] = datasets["tas"]

    with pytest.raises(ValueError, match="unexpected"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_units():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["tas"].variables["tas"].units = "K"

    with pytest.raises(ValueError, match="expected units"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_source_dtype():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["hurs"].variables["hurs"].dtype = np.dtype("float32")

    with pytest.raises(ValueError, match="expected int32"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_dimensions():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["tas"].variables["tas"].dimensions = ("time", "X", "Y")

    with pytest.raises(ValueError, match="unexpected dimensions"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_field_shape():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["hurs"].variables["hurs"].shape = (719, 938, 720)

    with pytest.raises(ValueError, match="expected shape"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_scale_factor():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["sfcWind"].variables["sfcWind"].scale_factor = 1.0

    with pytest.raises(ValueError, match="scale factor"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_fill_value():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["hurs"].variables["hurs"]._FillValue = -1

    with pytest.raises(ValueError, match="fill value"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_misaligned_coordinates():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["hurs"].variables["X"].values[100] += 1_000.0

    with pytest.raises(ValueError, match="X coordinates do not match"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_unexpected_canonical_grid():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    for dataset in datasets.values():
        dataset.variables["Y"].values[0] += 1_000.0

    with pytest.raises(ValueError, match="Y coordinates do not match"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_incorrect_crs():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["tas"].variables["crs"].epsg_code = "EPSG:25833"

    with pytest.raises(ValueError, match="unexpected CRS"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_missing_grid_mapping():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["tas"].variables["tas"].grid_mapping = "missing_crs"

    with pytest.raises(ValueError, match="missing grid-mapping"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_mismatched_crs_metadata():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["sfcWind"].variables["crs"].standard_parallel = np.asarray(
        [35.0, 60.0]
    )

    with pytest.raises(ValueError, match="CRS metadata does not match"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_nonconsecutive_timestamps():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    for dataset in datasets.values():
        dataset.variables["time"].values[20] += 1.0 / 24.0

    with pytest.raises(ValueError, match="complete consecutive UTC month"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_wrong_time_units():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    for dataset in datasets.values():
        dataset.variables["time"].units = "hours since 1949-12-01"

    with pytest.raises(ValueError, match="unexpected units"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_missing_coordinate():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    del datasets["tas"].variables["Y"]

    with pytest.raises(ValueError, match="missing coordinate"):
        validate_hostrada_month(datasets, month)


def test_hostrada_month_validation_rejects_mismatched_time_metadata():
    month = HostradaMonthKey.from_partition_key("2026-06")
    datasets = _month_datasets(month)
    datasets["hurs"].variables["time"].calendar = "standard"

    with pytest.raises(ValueError, match="time metadata does not match"):
        validate_hostrada_month(datasets, month)
