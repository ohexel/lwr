from datetime import date

from src.database.connection import database_connection


def test_hostrada_spatial_bridge_builds_is_idempotent_and_detects_damage():
    geography_version = "hostrada_bridge_test_geography"
    source_grid_id = "hostrada_bridge_test_grid"
    x_origin = 4_203_500.0
    y_origin = 2_845_500.0

    with database_connection(
        application_name="capstone_hostrada_spatial_test"
    ) as connection:
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
                %s, %s, 'HOSTRADA-v1-0', 3034, 25833,
                %s, %s, 3, 1, 1000.0, 1000.0
            )
            """,
            (
                source_grid_id,
                "f" * 64,
                x_origin,
                y_origin,
            ),
        )

        connection.execute(
            """
            INSERT INTO normalized.plr (
                plr_id,
                geometry,
                geography_version,
                reference_date,
                source_sha256
            )
            VALUES
            (
                'HOSTRADA_A',
                ST_Multi(
                    ST_Transform(
                        ST_MakeEnvelope(
                            %s - 400.0,
                            %s - 400.0,
                            %s + 1400.0,
                            %s + 400.0,
                            3034
                        ),
                        25833
                    )
                ),
                %s,
                %s,
                'hostrada_bridge_test_fixture'
            ),
            (
                'HOSTRADA_B',
                ST_Multi(
                    ST_Transform(
                        ST_MakeEnvelope(
                            %s + 1600.0,
                            %s - 400.0,
                            %s + 2400.0,
                            %s + 400.0,
                            3034
                        ),
                        25833
                    )
                ),
                %s,
                %s,
                'hostrada_bridge_test_fixture'
            )
            """,
            (
                x_origin,
                y_origin,
                x_origin,
                y_origin,
                geography_version,
                date(2099, 1, 1),
                x_origin,
                y_origin,
                x_origin,
                y_origin,
                geography_version,
                date(2099, 1, 1),
            ),
        )

        cells = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_hostrada_cell_geometry(
                %s::TEXT,
                %s::TEXT
            )
            """,
            (geography_version, source_grid_id),
        ).fetchone()

        assert cells == (3, 2, 3)

        bridge = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_hostrada_plr_area_bridge(
                %s::TEXT,
                %s::TEXT
            )
            """,
            (geography_version, source_grid_id),
        ).fetchone()

        assert bridge == (3, 2, 3)

        weights = connection.execute(
            """
            SELECT
                plr_id,
                x_index,
                fraction_of_plr,
                fraction_of_hostrada_cell
            FROM normalized.hostrada_plr_area_bridge
            WHERE geography_version = %s
              AND source_grid_id = %s
            ORDER BY plr_id, x_index
            """,
            (geography_version, source_grid_id),
        ).fetchall()

        assert len(weights) == 3
        assert weights[0][0] == "HOSTRADA_A"
        assert weights[1][0] == "HOSTRADA_A"
        assert abs(float(weights[0][2]) + float(weights[1][2]) - 1.0) < 1e-6
        assert 0.49 < float(weights[0][2]) < 0.51
        assert 0.49 < float(weights[1][2]) < 0.51
        assert weights[2][0] == "HOSTRADA_B"
        assert abs(float(weights[2][2]) - 1.0) < 1e-6

        quality = connection.execute(
            """
            SELECT *
            FROM normalized.check_hostrada_plr_area_bridge_quality(
                %s::TEXT,
                %s::TEXT,
                %s::INTEGER
            )
            """,
            (geography_version, source_grid_id, 2),
        ).fetchone()

        assert quality is not None
        assert quality[0] is True
        assert quality[1:6] == (3, 2, 2, 3, 3)
        assert quality[6:13] == (0, 0, 0, 0, 0, 0, 0)
        assert float(quality[13]) < 1e-6

        rerun = connection.execute(
            """
            SELECT *
            FROM normalized.refresh_hostrada_plr_area_bridge(
                %s::TEXT,
                %s::TEXT
            )
            """,
            (geography_version, source_grid_id),
        ).fetchone()

        assert rerun == bridge

        connection.execute(
            """
            DELETE FROM normalized.hostrada_plr_area_bridge
            WHERE geography_version = %s
              AND source_grid_id = %s
              AND plr_id = 'HOSTRADA_A'
              AND x_index = 1
            """,
            (geography_version, source_grid_id),
        )

        damaged = connection.execute(
            """
            SELECT
                passed,
                unused_hostrada_cell_count,
                plr_weight_failure_count
            FROM normalized.check_hostrada_plr_area_bridge_quality(
                %s::TEXT,
                %s::TEXT,
                %s::INTEGER
            )
            """,
            (geography_version, source_grid_id, 2),
        ).fetchone()

        assert damaged == (False, 1, 1)

        connection.rollback()
