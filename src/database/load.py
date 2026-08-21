from dataclasses import dataclass
from time import perf_counter
from typing import Iterable, Sequence

from psycopg import Connection, sql

@dataclass(frozen=True)
class LoadResult:
    target_table: str
    row_count: int
    duration_seconds: float

def copy_rows(
    connection: Connection,
    *,
    schema: str,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> LoadResult:
    if not schema:
        raise ValueError("schema must not be empty")
    if not table:
        raise ValueError("table must not be empty")
    if not columns:
        raise ValueError("columns must not be empty")
    if len(set(columns)) != len(columns):
        raise ValueError("columns must not contain duplicates")

    statement = sql.SQL(
        "COPY {}.{} ({}) FROM STDIN"
    ).format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(
            sql.Identifier(column)
            for column in columns
        ),
    )

    started = perf_counter()
    row_count = 0

    with connection.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for row in rows:
                if len(row) != len(columns):
                    raise ValueError(
                        "COPY row length does not match column count "
                        f"for {schema}.{table}: "
                        f"expected {len(columns)}, received {len(row)}"
                    )
                copy.write_row(row)
                row_count += 1

    return LoadResult(
        target_table=f"{schema}.{table}",
        row_count=row_count,
        duration_seconds=perf_counter() - started,
    )
