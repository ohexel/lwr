"""Source, grid, and monthly partition contracts for DWD HOSTRADA."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import numpy as np


HOSTRADA_DATASET_VERSION = "HOSTRADA-v1-0"
HOSTRADA_SOURCE_SRID = 3034
HOSTRADA_TARGET_SRID = 25833
HOSTRADA_TIME_UNITS = "days since 1949-12-01T00:00:00+00:00"
HOSTRADA_TIME_CALENDAR = "proleptic_gregorian"
HOSTRADA_TIME_EPOCH = datetime(1949, 12, 1, tzinfo=timezone.utc)
HOSTRADA_MONTH_FORMAT = "%Y-%m"

_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")
_CRS_ATTRIBUTES = (
    "epsg_code",
    "grid_mapping_name",
    "standard_parallel",
    "latitude_of_projection_origin",
    "longitude_of_projection_origin",
    "false_easting",
    "false_northing",
    "semi_major_axis",
    "inverse_flattening",
    "proj4",
    "crs_wkt",
    "spatial_ref",
)


@dataclass(frozen=True)
class HostradaMonthKey:
    """One complete UTC source month, independent of forecast partitions."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not isinstance(self.year, int) or isinstance(self.year, bool):
            raise ValueError("HOSTRADA partition year must be an integer")
        if not isinstance(self.month, int) or isinstance(self.month, bool):
            raise ValueError("HOSTRADA partition month must be an integer")
        if not 1 <= self.year <= 9998:
            raise ValueError("HOSTRADA partition year must be between 1 and 9998")
        if not 1 <= self.month <= 12:
            raise ValueError("HOSTRADA partition month must be between 1 and 12")

    @classmethod
    def from_partition_key(cls, value: str) -> "HostradaMonthKey":
        if not isinstance(value, str):
            raise ValueError("HOSTRADA monthly partition must use YYYY-MM")

        match = _MONTH_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("HOSTRADA monthly partition must use YYYY-MM")

        return cls(
            year=int(match.group("year")),
            month=int(match.group("month")),
        )

    @property
    def partition_key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def start_utc(self) -> datetime:
        return datetime(self.year, self.month, 1, tzinfo=timezone.utc)

    @property
    def end_utc(self) -> datetime:
        if self.month == 12:
            return datetime(self.year + 1, 1, 1, tzinfo=timezone.utc)
        return datetime(self.year, self.month + 1, 1, tzinfo=timezone.utc)

    @property
    def hour_count(self) -> int:
        return int((self.end_utc - self.start_utc) / timedelta(hours=1))

    def source_filename(self, variable_name: str) -> str:
        contract = get_hostrada_field(variable_name)
        final_hour = self.end_utc - timedelta(hours=1)
        return (
            f"{contract.variable_name}_1hr_{HOSTRADA_DATASET_VERSION}_BE_gn_"
            f"{self.start_utc:%Y%m%d%H}-{final_hour:%Y%m%d%H}.nc"
        )


@dataclass(frozen=True)
class HostradaFieldContract:
    variable_name: str
    source_directory: str
    units: str
    scale_factor: float = 0.1
    fill_value: int = -9999


HOSTRADA_FIELD_CONTRACTS = (
    HostradaFieldContract("tas", "air_temperature_mean", "celsius"),
    HostradaFieldContract("hurs", "humidity_relative", "%"),
    HostradaFieldContract("sfcWind", "wind_speed", "m s-1"),
)
HOSTRADA_REQUIRED_VARIABLES = tuple(
    field.variable_name for field in HOSTRADA_FIELD_CONTRACTS
)
_HOSTRADA_FIELDS_BY_NAME = {
    field.variable_name: field for field in HOSTRADA_FIELD_CONTRACTS
}


def get_hostrada_field(variable_name: str) -> HostradaFieldContract:
    try:
        return _HOSTRADA_FIELDS_BY_NAME[variable_name]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Unsupported HOSTRADA variable: {variable_name!r}"
        ) from exc


@dataclass(frozen=True)
class HostradaGridContract:
    x_origin_m: float
    y_origin_m: float
    x_count: int
    y_count: int
    x_spacing_m: float
    y_spacing_m: float
    source_srid: int = HOSTRADA_SOURCE_SRID
    target_srid: int = HOSTRADA_TARGET_SRID

    @property
    def x_coordinates(self) -> np.ndarray:
        return self.x_origin_m + np.arange(self.x_count, dtype=np.float64) * (
            self.x_spacing_m
        )

    @property
    def y_coordinates(self) -> np.ndarray:
        return self.y_origin_m + np.arange(self.y_count, dtype=np.float64) * (
            self.y_spacing_m
        )

    @property
    def grid_fingerprint(self) -> str:
        return hostrada_grid_fingerprint(
            self.x_coordinates,
            self.y_coordinates,
            source_srid=self.source_srid,
        )

    @property
    def source_grid_id(self) -> str:
        return f"hostrada_v1_0_{self.grid_fingerprint[:16]}"


def hostrada_grid_fingerprint(
    x_coordinates: np.ndarray,
    y_coordinates: np.ndarray,
    *,
    source_srid: int = HOSTRADA_SOURCE_SRID,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"{HOSTRADA_DATASET_VERSION}|EPSG:{source_srid}|X|".encode())
    digest.update(np.asarray(x_coordinates, dtype="<f8").tobytes(order="C"))
    digest.update(b"|Y|")
    digest.update(np.asarray(y_coordinates, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


HOSTRADA_GRID_CONTRACT = HostradaGridContract(
    x_origin_m=3_670_500.0,
    y_origin_m=2_242_500.0,
    x_count=720,
    y_count=938,
    x_spacing_m=1_000.0,
    y_spacing_m=1_000.0,
)


@dataclass(frozen=True)
class ValidatedHostradaMonth:
    month: HostradaMonthKey
    source_grid_id: str
    grid_fingerprint: str
    hour_count: int
    first_utc: datetime
    last_utc: datetime
    variables: tuple[str, ...]


def _axis_values(dataset: Any, name: str) -> np.ndarray:
    variable = dataset.variables.get(name)
    if variable is None:
        raise ValueError(f"HOSTRADA dataset is missing coordinate {name!r}")

    values = np.asarray(variable[:])
    if values.ndim != 1 or values.size == 0:
        raise ValueError(f"HOSTRADA coordinate {name!r} must be one-dimensional")
    if not np.issubdtype(values.dtype, np.number):
        raise ValueError(f"HOSTRADA coordinate {name!r} must be numeric")
    if not np.isfinite(values).all():
        raise ValueError(f"HOSTRADA coordinate {name!r} has non-finite values")
    return values


def _normalized_attribute(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return tuple(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def _crs_signature(dataset: Any, variable: Any) -> tuple[Any, ...]:
    mapping_name = getattr(variable, "grid_mapping", None)
    if not isinstance(mapping_name, str) or mapping_name not in dataset.variables:
        raise ValueError("HOSTRADA field has a missing grid-mapping variable")

    mapping = dataset.variables[mapping_name]
    epsg_code = str(getattr(mapping, "epsg_code", ""))
    if epsg_code != f"EPSG:{HOSTRADA_GRID_CONTRACT.source_srid}":
        raise ValueError(f"HOSTRADA field has unexpected CRS {epsg_code!r}")

    return tuple(
        (attribute, _normalized_attribute(getattr(mapping, attribute, None)))
        for attribute in _CRS_ATTRIBUTES
    )


def _validate_field(
    dataset: Any,
    contract: HostradaFieldContract,
    month: HostradaMonthKey,
) -> tuple[Any, tuple[Any, ...]]:
    variable = dataset.variables.get(contract.variable_name)
    if variable is None:
        raise ValueError(
            f"HOSTRADA dataset is missing variable {contract.variable_name!r}"
        )

    if tuple(variable.dimensions) != ("time", "Y", "X"):
        raise ValueError(
            f"{contract.variable_name}: unexpected dimensions "
            f"{tuple(variable.dimensions)!r}"
        )

    expected_shape = (
        month.hour_count,
        HOSTRADA_GRID_CONTRACT.y_count,
        HOSTRADA_GRID_CONTRACT.x_count,
    )
    if tuple(variable.shape) != expected_shape:
        raise ValueError(
            f"{contract.variable_name}: expected shape {expected_shape}, "
            f"got {tuple(variable.shape)}"
        )

    if np.dtype(variable.dtype) != np.dtype("int32"):
        raise ValueError(
            f"{contract.variable_name}: expected int32 source values, "
            f"got {np.dtype(variable.dtype)}"
        )

    units = str(getattr(variable, "units", ""))
    if units != contract.units:
        raise ValueError(
            f"{contract.variable_name}: expected units {contract.units!r}, "
            f"got {units!r}"
        )

    scale_factor = getattr(variable, "scale_factor", None)
    if scale_factor is None or not np.isclose(
        float(scale_factor),
        contract.scale_factor,
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError(
            f"{contract.variable_name}: unexpected scale factor {scale_factor!r}"
        )

    fill_value = getattr(variable, "_FillValue", None)
    if fill_value is None or int(fill_value) != contract.fill_value:
        raise ValueError(
            f"{contract.variable_name}: unexpected fill value {fill_value!r}"
        )

    return variable, _crs_signature(dataset, variable)


def _validate_time_coordinate(
    time_variable: Any,
    values: np.ndarray,
    month: HostradaMonthKey,
) -> None:
    units = str(getattr(time_variable, "units", ""))
    calendar = str(getattr(time_variable, "calendar", ""))

    if units != HOSTRADA_TIME_UNITS:
        raise ValueError(f"HOSTRADA time coordinate has unexpected units {units!r}")
    if calendar != HOSTRADA_TIME_CALENDAR:
        raise ValueError(
            f"HOSTRADA time coordinate has unexpected calendar {calendar!r}"
        )
    if values.size != month.hour_count:
        raise ValueError(
            f"HOSTRADA month {month.partition_key} requires {month.hour_count} "
            f"hourly observations, got {values.size}"
        )

    start_days = (month.start_utc - HOSTRADA_TIME_EPOCH) / timedelta(days=1)
    expected = float(start_days) + np.arange(month.hour_count, dtype=np.float64) / 24

    if not np.allclose(
        values.astype(np.float64),
        expected,
        rtol=0.0,
        atol=1e-9,
    ):
        raise ValueError(
            f"HOSTRADA timestamps do not form the complete consecutive UTC "
            f"month {month.partition_key}"
        )


def validate_hostrada_month(
    datasets: Mapping[str, Any],
    month: HostradaMonthKey,
) -> ValidatedHostradaMonth:
    """Validate three opened NetCDF datasets without reading weather values."""

    required = set(HOSTRADA_REQUIRED_VARIABLES)
    supplied = set(datasets)
    if supplied != required:
        missing = sorted(required - supplied)
        unexpected = sorted(supplied - required)
        raise ValueError(
            f"HOSTRADA source fields must be exactly {HOSTRADA_REQUIRED_VARIABLES}; "
            f"missing={missing}, unexpected={unexpected}"
        )

    baseline_axes: dict[str, np.ndarray] | None = None
    baseline_signature: tuple[Any, ...] | None = None
    baseline_time_metadata: tuple[str, str] | None = None

    for contract in HOSTRADA_FIELD_CONTRACTS:
        dataset = datasets[contract.variable_name]
        _, signature = _validate_field(dataset, contract, month)
        axes = {
            axis_name: _axis_values(dataset, axis_name)
            for axis_name in ("X", "Y", "time")
        }
        time_variable = dataset.variables["time"]
        time_metadata = (
            str(getattr(time_variable, "units", "")),
            str(getattr(time_variable, "calendar", "")),
        )

        if baseline_axes is None:
            baseline_axes = axes
            baseline_signature = signature
            baseline_time_metadata = time_metadata
        else:
            for axis_name, expected in baseline_axes.items():
                if not np.array_equal(axes[axis_name], expected):
                    raise ValueError(
                        f"{contract.variable_name}: {axis_name} coordinates "
                        "do not match tas exactly"
                    )
            if signature != baseline_signature:
                raise ValueError(
                    f"{contract.variable_name}: CRS metadata does not match tas"
                )
            if time_metadata != baseline_time_metadata:
                raise ValueError(
                    f"{contract.variable_name}: time metadata does not match tas"
                )

    assert baseline_axes is not None
    if not np.array_equal(
        baseline_axes["X"],
        HOSTRADA_GRID_CONTRACT.x_coordinates,
    ):
        raise ValueError("HOSTRADA X coordinates do not match the canonical grid")
    if not np.array_equal(
        baseline_axes["Y"],
        HOSTRADA_GRID_CONTRACT.y_coordinates,
    ):
        raise ValueError("HOSTRADA Y coordinates do not match the canonical grid")

    _validate_time_coordinate(
        datasets["tas"].variables["time"],
        baseline_axes["time"],
        month,
    )

    fingerprint = hostrada_grid_fingerprint(
        baseline_axes["X"],
        baseline_axes["Y"],
    )
    if fingerprint != HOSTRADA_GRID_CONTRACT.grid_fingerprint:
        raise ValueError("HOSTRADA grid fingerprint does not match the contract")

    return ValidatedHostradaMonth(
        month=month,
        source_grid_id=HOSTRADA_GRID_CONTRACT.source_grid_id,
        grid_fingerprint=fingerprint,
        hour_count=month.hour_count,
        first_utc=month.start_utc,
        last_utc=month.end_utc - timedelta(hours=1),
        variables=HOSTRADA_REQUIRED_VARIABLES,
    )
