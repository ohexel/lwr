from datetime import date

from src.database.connection import database_connection


def test_postgis_bridge_area_weights():
    geography_version = "phase_6_fixture"
    source_grid_id = "phase_6_grid"

    with database_connection(
        application_name="capstone_phase_6_test"
    ) as connection:
        connection.execute(
            """
            INSERT INTO normalized.plr (
                plr_id,
                geometry,
                geography_version,
                reference_date,
                source_sha256
            )
            VALUES (
                'A',
                ST_Multi(
                    ST_GeomFromText(
                        'POLYGON(('
                        '390000 5819000,'
                        '390100 5819000,'
                        '390100 5819100,'
                        '390000 5819100,'
                        '390000 5819000'
                        '))',
                        25833
                    )
                ),
                %s,
                %s,
                'phase_6_fixture'
            )
            """,
            (
                geography_version,
                date(2099, 1, 1),
            ),
        )

        connection.execute(
            """
            INSERT INTO normalized.icon_cell (
                source_grid_id,
                cell_index,
                geometry,
                icon_cell_area_m2
            )
            VALUES
            (
                %s,
                1,
                ST_GeomFromText(
                    'POLYGON(('
                    '390000 5819000,'
                    '390100 5819000,'
                    '390100 5819100,'
                    '390000 5819000'
                    '))',
                    25833
                ),
                5000
            ),
            (
                %s,
                2,
                ST_GeomFromText(
                    'POLYGON(('
                    '390000 5819000,'
                    '390100 5819100,'
                    '390000 5819100,'
                    '390000 5819000'
                    '))',
                    25833
                ),
                5000
            )
            """,
            (
                source_grid_id,
                source_grid_id,
            ),
        )

        summary = connection.execute(
            """
            SELECT
                bridge.bridge_row_count,
                bridge.represented_plr_count,
                bridge.intersecting_icon_cell_count
            FROM normalized.refresh_icon_plr_area_bridge(
                %s,
                %s,
                %s,
                %s
            ) AS bridge
            """,
            (
                geography_version,
                source_grid_id,
                1,
                2,
            ),
        ).fetchone()

        assert summary == (2, 1, 2)

        rows = connection.execute(
            """
            SELECT
                bridge.cell_index,
                bridge.intersection_area_m2,
                bridge.fraction_of_plr,
                bridge.fraction_of_icon_cell
            FROM normalized.icon_plr_area_bridge AS bridge
            WHERE bridge.geography_version = %s
              AND bridge.source_grid_id = %s
            ORDER BY bridge.cell_index
            """,
            (
                geography_version,
                source_grid_id,
            ),
        ).fetchall()

        assert len(rows) == 2

        for row in rows:
            assert abs(row[1] - 5000.0) < 0.001
            assert abs(row[2] - 0.5) < 0.000001
            assert abs(row[3] - 1.0) < 0.000001

        quality = connection.execute(
            """
            SELECT
                quality.passed,
                quality.uncovered_plr_count,
                quality.weight_sum_failure_count,
                quality.max_fraction_of_plr_deviation
            FROM normalized.check_icon_plr_area_bridge(
                %s,
                %s,
                %s
            ) AS quality
            """,
            (
                geography_version,
                source_grid_id,
                1,
            ),
        ).fetchone()

        assert quality[0] is True
        assert quality[1] == 0
        assert quality[2] == 0
        assert quality[3] < 0.000001

        connection.rollback()
