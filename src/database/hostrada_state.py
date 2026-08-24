"""Database state for the canonical, Berlin-scoped HOSTRADA grid."""

from __future__ import annotations

from psycopg import Connection

from src.hostrada_contract import (
    HOSTRADA_DATASET_VERSION,
    HOSTRADA_GRID_CONTRACT,
)


def ensure_hostrada_grid(connection: Connection) -> str:
    contract = HOSTRADA_GRID_CONTRACT

    expected = (
        contract.source_grid_id,
        contract.grid_fingerprint,
        HOSTRADA_DATASET_VERSION,
        contract.source_srid,
        contract.target_srid,
        contract.x_origin_m,
        contract.y_origin_m,
        contract.x_count,
        contract.y_count,
        contract.x_spacing_m,
        contract.y_spacing_m,
    )

    connection.execute(
        """
        INSERT INTO normalized.hostrada_grid (
            source_grid_id,
            grid_fingerprint,
            dataset_version,
            source_srid,
            target_srid,
            x_origin_m,
            y_origin_m,
            x_count,
            y_count,
            x_spacing_m,
            y_spacing_m
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (source_grid_id) DO NOTHING
        """,
        expected,
    )

    observed = connection.execute(
        """
        SELECT
            source_grid_id,
            grid_fingerprint,
            dataset_version,
            source_srid,
            target_srid,
            x_origin_m,
            y_origin_m,
            x_count,
            y_count,
            x_spacing_m,
            y_spacing_m
        FROM normalized.hostrada_grid
        WHERE source_grid_id = %s
        """,
        (contract.source_grid_id,),
    ).fetchone()

    if observed is None or tuple(observed) != expected:
        raise RuntimeError(
            "Registered HOSTRADA grid does not match the canonical contract: "
            f"{contract.source_grid_id}"
        )

    return contract.source_grid_id
