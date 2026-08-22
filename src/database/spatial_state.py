from psycopg import Connection


def current_geography_version(
    connection: Connection,
) -> str:
    row = connection.execute(
        """
        SELECT
            plr_row.geography_version
        FROM normalized.plr AS plr_row
        GROUP BY plr_row.geography_version
        ORDER BY
            MAX(plr_row.reference_date)
                DESC NULLS LAST,
            plr_row.geography_version DESC
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        raise RuntimeError(
            "normalized.plr contains no geography version"
        )

    return str(row[0])
