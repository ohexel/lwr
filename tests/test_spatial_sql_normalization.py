from datetime import date

from src.database.connection import database_connection
from src.database.load import copy_rows


def test_postgis_normalizes_plr_and_icon_geometry():
    lor_source = "phase_5_lor_fixture"
    grid_id = "phase_5_icon_fixture"

    with database_connection(
        application_name="capstone_phase_5_test"
    ) as connection:
        copy_rows(
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
            rows=[
                (
                    "A",
                    (
                        "SRID=25833;"
                        "POLYGON(("
                        "390000 5819000,"
                        "390100 5819000,"
                        "390100 5819100,"
                        "390000 5819100,"
                        "390000 5819000"
                        "))"
                    ),
                    "EPSG:25833",
                    "fixture_v1",
                    date(2099, 1, 1),
                    "fixture.geojson",
                    lor_source,
                    "fixture",
                    "fixture",
                    "fixture",
                )
            ],
        )

        plr_summary = connection.execute(
            """
            SELECT
                result.source_row_count,
                result.normalized_row_count,
                result.rejected_row_count
            FROM normalized.refresh_plr_geometry(%s, %s)
                AS result
            """,
            (lor_source, 1),
        ).fetchone()

        assert plr_summary == (1, 1, 0)

        plr_geometry = connection.execute(
            """
            SELECT
                ST_SRID(plr.geometry),
                GeometryType(plr.geometry),
                ST_IsValid(plr.geometry),
                ST_Area(plr.geometry)
            FROM normalized.plr AS plr
            WHERE plr.source_sha256 = %s
            """,
            (lor_source,),
        ).fetchone()

        assert plr_geometry[0] == 25833
        assert plr_geometry[1] == "MULTIPOLYGON"
        assert plr_geometry[2] is True
        assert plr_geometry[3] > 0

        plr_quality = connection.execute(
            """
            SELECT
                quality.passed
            FROM normalized.check_plr_geometry_quality(
                %s,
                %s
            ) AS quality
            """,
            (lor_source, 1),
        ).fetchone()

        assert plr_quality[0] is True

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
                grid_id,
                "fixture.nc",
                "fixture_grid_hash",
                "fixture",
                3,
                1,
            ),
        )

        copy_rows(
            connection,
            schema="raw",
            table="icon_grid_vertex",
            columns=(
                "source_grid_id",
                "vertex_index",
                "longitude_deg",
                "latitude_deg",
            ),
            rows=[
                (grid_id, 0, 13.40, 52.50),
                (grid_id, 1, 13.41, 52.50),
                (grid_id, 2, 13.405, 52.51),
            ],
        )

        copy_rows(
            connection,
            schema="raw",
            table="icon_grid_cell_vertex",
            columns=(
                "source_grid_id",
                "cell_index",
                "vertex_order",
                "vertex_index",
            ),
            rows=[
                (grid_id, 0, 0, 0),
                (grid_id, 0, 1, 1),
                (grid_id, 0, 2, 2),
            ],
        )

        icon_summary = connection.execute(
            """
            SELECT
                result.raw_vertex_count,
                result.raw_cell_count,
                result.normalized_cell_count,
                result.rejected_cell_count
            FROM normalized.refresh_icon_cell_geometry(
                %s,
                %s,
                %s
            ) AS result
            """,
            (grid_id, 3, 1),
        ).fetchone()

        assert icon_summary == (3, 1, 1, 0)

        icon_geometry = connection.execute(
            """
            SELECT
                ST_SRID(cell.geometry),
                GeometryType(cell.geometry),
                ST_IsValid(cell.geometry),
                ST_NPoints(
                    ST_ExteriorRing(cell.geometry)
                ),
                cell.icon_cell_area_m2
            FROM normalized.icon_cell AS cell
            WHERE cell.source_grid_id = %s
              AND cell.cell_index = 0
            """,
            (grid_id,),
        ).fetchone()

        assert icon_geometry[0] == 25833
        assert icon_geometry[1] == "POLYGON"
        assert icon_geometry[2] is True
        assert icon_geometry[3] == 4
        assert icon_geometry[4] > 0

        icon_quality = connection.execute(
            """
            SELECT quality.passed
            FROM normalized.check_icon_geometry_quality(
                %s,
                %s,
                %s
            ) AS quality
            """,
            (grid_id, 3, 1),
        ).fetchone()

        assert icon_quality[0] is True

        connection.rollback()
