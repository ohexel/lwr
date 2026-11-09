"""Load verified Germany-layer sources into the raw schema.

Each load is one transaction: rows previously loaded from the same source
checksum are replaced, rows from other sources are never touched.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pyogrio
import shapely
from psycopg import Connection

from src.database.connection import database_connection
from src.database.load import copy_rows
from src.germany.acquire import sha256_file
from src.germany.contract import (
    VG250_CONTRACT,
    ZENSUS_CONTRACT,
    ZensusContract,
    ZensusExtract,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "germany" / "raw_schema.sql"
)

VG250_ATTRIBUTES: tuple[str, ...] = (
    "OBJID", "BEGINN", "ADE", "GF", "BSG", "ARS", "AGS", "SDV_ARS", "GEN",
    "BEZ", "IBZ", "BEM", "NBD", "SN_L", "SN_R", "SN_K", "SN_V1", "SN_V2",
    "SN_G", "FK_S3", "NUTS", "ARS_0", "AGS_0", "WSK", "DLM_ID",
)

ZENSUS_CSV_COLUMNS: tuple[str, ...] = (
    "statistics_code", "statistics_label", "time_code", "time_label", "time",
    "1_variable_code", "1_variable_label", "1_variable_attribute_code",
    "1_variable_attribute_label", "2_variable_code", "2_variable_label",
    "2_variable_attribute_code", "2_variable_attribute_label", "value",
    "value_unit", "value_variable_code", "value_variable_label",
)

ZENSUS_TABLE_COLUMNS: tuple[str, ...] = (
    "statistics_code", "statistics_label", "time_code", "time_label",
    "time_value", "variable_1_code", "variable_1_label",
    "variable_1_attribute_code", "variable_1_attribute_label",
    "variable_2_code", "variable_2_label", "variable_2_attribute_code",
    "variable_2_attribute_label", "value", "value_unit",
    "value_variable_code", "value_variable_label",
)


@dataclass(frozen=True)
class RawLoadResult:
    target_table: str
    source_path: str
    source_sha256: str
    row_count: int
    duration_seconds: float


def apply_germany_schema(connection: Connection) -> None:
    """Apply the additive, idempotent Germany DDL."""
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _text_or_none(value: object) -> str | None:
    if _is_missing(value):
        return None
    text = str(value)
    return text if text != "" else None


def _int_or_none(value: object) -> int | None:
    if _is_missing(value) or value == "":
        return None
    return int(value)


INTEGER_ATTRIBUTES = frozenset({"ADE", "GF", "BSG", "IBZ"})


def vg250_rows(
    geopackage: Path,
    level: str,
    *,
    archive_sha256: str,
) -> Iterator[Sequence[object]]:
    contract = VG250_CONTRACT
    layer = contract.layers[level]
    frame = pyogrio.read_dataframe(
        geopackage,
        layer=layer,
        datetime_as_string=True,
    )

    epsg = None if frame.crs is None else frame.crs.to_epsg()
    if epsg != contract.source_crs_epsg:
        raise RuntimeError(
            f"VG250 layer {layer} has EPSG {epsg}, "
            f"expected {contract.source_crs_epsg}"
        )
    missing = [name for name in VG250_ATTRIBUTES if name not in frame.columns]
    if missing:
        raise RuntimeError(f"VG250 layer {layer} lacks attributes {missing}")

    geometries = shapely.to_wkb(
        shapely.set_srid(frame.geometry.values, epsg),
        hex=True,
        include_srid=True,
    )
    records = frame[list(VG250_ATTRIBUTES)].to_dict("records")

    for record, geometry_hex in zip(records, geometries):
        attributes = [
            _int_or_none(record[name])
            if name in INTEGER_ATTRIBUTES
            else _text_or_none(record[name])
            for name in VG250_ATTRIBUTES
        ]
        yield (
            level,
            *attributes,
            geometry_hex,
            contract.vintage,
            layer,
            str(geopackage),
            archive_sha256,
            contract.source_url,
            contract.publisher,
            contract.license,
        )


def load_vg250_raw(
    project_root: Path = Path("."),
) -> list[RawLoadResult]:
    contract = VG250_CONTRACT
    archive = project_root / contract.archive_path
    geopackage = archive.parent / Path(contract.geopackage_member).name

    archive_sha256 = sha256_file(archive)
    if archive_sha256 != contract.archive_sha256:
        raise RuntimeError(
            f"VG250 archive checksum {archive_sha256} does not match the "
            f"contract {contract.archive_sha256}"
        )
    if not geopackage.exists():
        raise FileNotFoundError(
            f"{geopackage} missing; run python -m src.germany.acquire"
        )

    columns = (
        "level",
        *(name.lower() for name in VG250_ATTRIBUTES),
        "geometry_source",
        "vintage",
        "source_layer",
        "source_path",
        "source_sha256",
        "source_url",
        "publisher",
        "license",
    )
    results: list[RawLoadResult] = []

    with database_connection(application_name="lwr_germany_vg250_load") as connection:
        apply_germany_schema(connection)
        connection.execute(
            "DELETE FROM raw.de_vg250 WHERE source_sha256 = %s",
            (archive_sha256,),
        )
        for level in contract.feature_counts:
            loaded = copy_rows(
                connection,
                schema="raw",
                table="de_vg250",
                columns=columns,
                rows=vg250_rows(geopackage, level, archive_sha256=archive_sha256),
            )
            expected_total, _ = contract.feature_counts[level]
            if loaded.row_count != expected_total:
                raise RuntimeError(
                    f"VG250 level {level}: loaded {loaded.row_count} features, "
                    f"contract expects {expected_total}"
                )
            results.append(
                RawLoadResult(
                    target_table=f"raw.de_vg250[{level}]",
                    source_path=str(geopackage),
                    source_sha256=archive_sha256,
                    row_count=loaded.row_count,
                    duration_seconds=loaded.duration_seconds,
                )
            )

    return results


def parse_zensus_flat_csv(text: str) -> Iterable[tuple[str | None, ...]]:
    """Parse a Zensus 'ffcsv' file; every value is kept as delivered text."""
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader, None)
    if header is None:
        raise ValueError("Zensus flat CSV is empty")
    header = [column.lstrip("﻿") for column in header]
    if tuple(header) != ZENSUS_CSV_COLUMNS:
        raise ValueError(
            f"Unexpected Zensus flat CSV header: {header}; "
            f"expected {list(ZENSUS_CSV_COLUMNS)}"
        )
    for line_number, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) != len(ZENSUS_CSV_COLUMNS):
            raise ValueError(
                f"Zensus flat CSV line {line_number} has {len(row)} fields, "
                f"expected {len(ZENSUS_CSV_COLUMNS)}"
            )
        yield tuple(value if value != "" else None for value in row)


def load_zensus_extract(
    connection: Connection,
    extract: ZensusExtract,
    project_root: Path = Path("."),
    contract: ZensusContract = ZENSUS_CONTRACT,
) -> RawLoadResult:
    path = project_root / extract.csv_path(contract.table_code)
    source_sha256 = sha256_file(path)
    if source_sha256 != extract.csv_sha256:
        raise RuntimeError(
            f"{path} has SHA-256 {source_sha256}, contract expects "
            f"{extract.csv_sha256}"
        )

    text = path.read_text(encoding="utf-8-sig")
    provenance = (
        str(path),
        source_sha256,
        contract.tablefile_url,
        contract.publisher,
        contract.license,
    )
    rows = (
        (contract.table_code, extract.regional_variable, *row, *provenance)
        for row in parse_zensus_flat_csv(text)
    )

    connection.execute(
        "DELETE FROM raw.de_zensus2022_age WHERE source_sha256 = %s",
        (source_sha256,),
    )
    loaded = copy_rows(
        connection,
        schema="raw",
        table="de_zensus2022_age",
        columns=(
            "table_code",
            "requested_regional_variable",
            *ZENSUS_TABLE_COLUMNS,
            "source_path",
            "source_sha256",
            "source_url",
            "publisher",
            "license",
        ),
        rows=rows,
    )
    return RawLoadResult(
        target_table="raw.de_zensus2022_age",
        source_path=str(path),
        source_sha256=source_sha256,
        row_count=loaded.row_count,
        duration_seconds=loaded.duration_seconds,
    )


def load_zensus_raw(
    project_root: Path = Path("."),
    contract: ZensusContract = ZENSUS_CONTRACT,
) -> list[RawLoadResult]:
    with database_connection(application_name="lwr_germany_zensus_load") as connection:
        apply_germany_schema(connection)
        return [
            load_zensus_extract(connection, extract, project_root, contract)
            for extract in contract.extracts
        ]
