import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Mapping

import psycopg
from psycopg import Connection

REQUIRED_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "DatabaseSettings":
        values = os.environ if environment is None else environment
        missing = [
            name
            for name in REQUIRED_ENVIRONMENT_VARIABLES
            if not values.get(name)
        ]
        if missing:
            raise RuntimeError(
                "Missing required PostgreSQL environment variables: "
                + ", ".join(missing)
            )

        try:
            port = int(values["POSTGRES_PORT"])
        except ValueError as exc:
            raise RuntimeError(
                "POSTGRES_PORT must be an integer"
            ) from exc

        return cls(
            host=values["POSTGRES_HOST"],
            port=port,
            database=values["POSTGRES_DB"],
            user=values["POSTGRES_USER"],
            password=values["POSTGRES_PASSWORD"],
        )

@contextmanager
def database_connection(
    settings: DatabaseSettings | None = None,
    *,
    application_name: str = "capstone",
) -> Iterator[Connection]:
    resolved = settings or DatabaseSettings.from_env()

    with psycopg.connect(
        host=resolved.host,
        port=resolved.port,
        dbname=resolved.database,
        user=resolved.user,
        password=resolved.password,
        application_name=application_name,
    ) as connection:
        yield connection

def database_health(connection: Connection) -> dict[str, str]:
    row = connection.execute(
        '''
        SELECT
            current_database(),
            current_user,
            current_setting('server_version'),
            postgis_version()
        '''
    ).fetchone()

    if row is None:
        raise RuntimeError("PostgreSQL health query returned no row")

    return {
        "database": str(row[0]),
        "user": str(row[1]),
        "postgres_version": str(row[2]),
        "postgis_version": str(row[3]),
    }
