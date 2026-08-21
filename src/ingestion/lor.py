import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import geopandas as gpd

from src.database.connection import database_connection
from src.database.load import copy_rows


DEFAULT_LOR_PATH = Path(
    "data/raw/berlin/lor/lor_planungsraum.geojson"
)

LOR_SOURCE_URL = (
    "https://daten.berlin.de/datensaetze/"
    "lebensweltlich-orientierte-raume-lor-"
    "01-01-2021-wfs-34c86848"
)

LOR_PUBLISHER = "Amt für Statistik Berlin-Brandenburg"
LOR_LICENSE = "CC BY 3.0 DE"
LOR_GEOGRAPHY_VERSION = "2023-01-01"
LOR_REFERENCE_DATE = date(2023, 1, 1)

PLR_ID_CANDIDATES = (
    "plr_id",
    "PLR_ID",
    "RAUMID",
    "raumid",
    "PLR",
    "plr",
)


@dataclass(frozen=True)
class LorLoadResult:
    source_path: str
    source_sha256: str
    target_table: str
    row_count: int
    source_crs: str | None
    duration_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()


def _find_plr_id_column(
    columns: list[str],
) -> str:
    for candidate in PLR_ID_CANDIDATES:
        if candidate in columns:
            return candidate

    raise ValueError(
        "Could not identify LOR PLR ID column. "
        f"Available columns: {columns}"
    )


def _geometry_ewkt(
    geometry,
    epsg: int | None,
) -> str | None:
    if geometry is None:
        return None

    if epsg is None:
        return geometry.wkt

    return f"SRID={epsg};{geometry.wkt}"


def load_lor_raw(
    source_path: Path = DEFAULT_LOR_PATH,
) -> LorLoadResult:
    if not source_path.exists():
        raise FileNotFoundError(
            f"LOR source not found: {source_path}"
        )

    source_sha256 = _sha256(source_path)
    frame = gpd.read_file(source_path)

    plr_id_column = _find_plr_id_column(
        list(frame.columns)
    )

    source_crs = (
        None
        if frame.crs is None
        else str(frame.crs)
    )

    epsg = (
        None
        if frame.crs is None
        else frame.crs.to_epsg()
    )

    rows = (
        (
            None
            if value is None
            else str(value).strip(),
            _geometry_ewkt(
                geometry,
                epsg,
            ),
            source_crs,
            LOR_GEOGRAPHY_VERSION,
            LOR_REFERENCE_DATE,
            str(source_path),
            source_sha256,
            LOR_SOURCE_URL,
            LOR_PUBLISHER,
            LOR_LICENSE,
        )
        for value, geometry in zip(
            frame[plr_id_column],
            frame.geometry,
        )
    )

    with database_connection(
        application_name="capstone_lor_load"
    ) as connection:
        connection.execute(
            """
            DELETE FROM raw.lor_plr
            WHERE source_sha256 = %s
            """,
            (source_sha256,),
        )

        result = copy_rows(
            connection,
            schema="raw",
            table="lor_plr",
            columns=(
                "plr_id_source",
                "geometry_source",
                "source_crs",
                "geography_version",
                "reference_date",
                "source_path",
                "source_sha256",
                "source_url",
                "publisher",
                "license",
            ),
            rows=rows,
        )

    return LorLoadResult(
        source_path=str(source_path),
        source_sha256=source_sha256,
        target_table=result.target_table,
        row_count=result.row_count,
        source_crs=source_crs,
        duration_seconds=result.duration_seconds,
    )
