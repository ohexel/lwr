import bz2
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from src.database.connection import database_connection
from src.database.load import copy_rows


DEFAULT_ICON_GRID_PATH = Path(
    "data/raw/icon_d2_grid/"
    "icon_grid_0047_R19B07_L.nc.bz2"
)

ICON_GRID_ID = "icon_grid_0047_R19B07_L"

ICON_GRID_URL = (
    "https://opendata.dwd.de/weather/lib/cdo/"
    "icon_grid_0047_R19B07_L.nc.bz2"
)

REQUIRED_VARIABLES = {
    "vlon",
    "vlat",
    "vertex_of_cell",
}


@dataclass(frozen=True)
class IconGridLoadResult:
    source_path: str
    source_sha256: str
    source_grid_id: str
    vertex_count: int
    cell_count: int
    topology_row_count: int
    vertex_load_seconds: float
    topology_load_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()


def _to_degrees(
    values: np.ndarray,
    units: str | None,
) -> np.ndarray:
    result = np.asarray(
        values,
        dtype="float64",
    ).reshape(-1)

    units_lower = (units or "").lower()

    if "radian" in units_lower:
        return np.rad2deg(result)

    if (
        result.size > 0
        and np.nanmax(np.abs(result))
        <= (2 * np.pi + 0.1)
    ):
        return np.rad2deg(result)

    return result


def _read_grid(
    source_path: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with tempfile.NamedTemporaryFile(
        suffix=".nc",
        delete=False,
    ) as temporary:
        netcdf_path = Path(temporary.name)

    try:
        with bz2.open(
            source_path,
            "rb",
        ) as source:
            with netcdf_path.open(
                "wb"
            ) as destination:
                shutil.copyfileobj(
                    source,
                    destination,
                )

        with Dataset(
            netcdf_path,
            "r",
        ) as dataset:
            missing = (
                REQUIRED_VARIABLES
                - set(dataset.variables)
            )
            if missing:
                raise ValueError(
                    "ICON grid is missing required variables: "
                    + ", ".join(sorted(missing))
                )

            longitude_var = dataset.variables[
                "vlon"
            ]
            latitude_var = dataset.variables[
                "vlat"
            ]

            longitude = _to_degrees(
                longitude_var[:],
                getattr(
                    longitude_var,
                    "units",
                    None,
                ),
            )
            latitude = _to_degrees(
                latitude_var[:],
                getattr(
                    latitude_var,
                    "units",
                    None,
                ),
            )

            connectivity = np.asarray(
                dataset.variables[
                    "vertex_of_cell"
                ][:]
            )

    finally:
        netcdf_path.unlink(
            missing_ok=True
        )

    if connectivity.shape[0] == 3:
        connectivity = connectivity.T

    if (
        connectivity.ndim != 2
        or connectivity.shape[1] != 3
    ):
        raise ValueError(
            "Unexpected ICON vertex_of_cell shape: "
            f"{connectivity.shape}"
        )

    connectivity = connectivity.astype(
        "int64"
    )

    minimum_index = int(
        connectivity.min()
    )

    if minimum_index == 1:
        connectivity -= 1
    elif minimum_index != 0:
        raise ValueError(
            "ICON vertex connectivity must use "
            "0-based or 1-based non-negative indexes; "
            f"minimum observed index is {minimum_index}"
        )

    if int(connectivity.max()) >= len(longitude):
        raise ValueError(
            "ICON vertex connectivity references "
            "a vertex outside the decoded vertex array"
        )

    if len(longitude) != len(latitude):
        raise ValueError(
            "ICON longitude/latitude vertex counts differ"
        )

    return (
        longitude,
        latitude,
        connectivity,
    )


def load_icon_grid_raw(
    source_path: Path = DEFAULT_ICON_GRID_PATH,
) -> IconGridLoadResult:
    if not source_path.exists():
        raise FileNotFoundError(
            f"ICON grid source not found: {source_path}"
        )

    source_sha256 = _sha256(
        source_path
    )

    (
        longitude,
        latitude,
        connectivity,
    ) = _read_grid(
        source_path
    )

    vertex_count = len(longitude)
    cell_count = len(connectivity)

    with database_connection(
        application_name="capstone_icon_grid_load"
    ) as connection:
        # Deleting the grid source cascades through vertices
        # and topology. PostgreSQL owns dependent-row cleanup.
        connection.execute(
            """
            DELETE FROM raw.icon_grid_source
            WHERE source_grid_id = %s
            """,
            (ICON_GRID_ID,),
        )

        connection.execute(
            """
            INSERT INTO raw.icon_grid_source (
                source_grid_id,
                source_path,
                source_sha256,
                source_url,
                vertex_count,
                cell_count
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                ICON_GRID_ID,
                str(source_path),
                source_sha256,
                ICON_GRID_URL,
                vertex_count,
                cell_count,
            ),
        )

        vertex_result = copy_rows(
            connection,
            schema="raw",
            table="icon_grid_vertex",
            columns=(
                "source_grid_id",
                "vertex_index",
                "longitude_deg",
                "latitude_deg",
            ),
            rows=(
                (
                    ICON_GRID_ID,
                    int(vertex_index),
                    float(longitude_value),
                    float(latitude_value),
                )
                for (
                    vertex_index,
                    (
                        longitude_value,
                        latitude_value,
                    ),
                ) in enumerate(
                    zip(
                        longitude,
                        latitude,
                    )
                )
            ),
        )

        topology_result = copy_rows(
            connection,
            schema="raw",
            table="icon_grid_cell_vertex",
            columns=(
                "source_grid_id",
                "cell_index",
                "vertex_order",
                "vertex_index",
            ),
            rows=(
                (
                    ICON_GRID_ID,
                    int(cell_index),
                    int(vertex_order),
                    int(connectivity[
                        cell_index,
                        vertex_order,
                    ]),
                )
                for cell_index in range(
                    cell_count
                )
                for vertex_order in range(3)
            ),
        )

    return IconGridLoadResult(
        source_path=str(source_path),
        source_sha256=source_sha256,
        source_grid_id=ICON_GRID_ID,
        vertex_count=vertex_count,
        cell_count=cell_count,
        topology_row_count=(
            topology_result.row_count
        ),
        vertex_load_seconds=(
            vertex_result.duration_seconds
        ),
        topology_load_seconds=(
            topology_result.duration_seconds
        ),
    )
