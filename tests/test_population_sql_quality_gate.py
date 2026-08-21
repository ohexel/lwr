from datetime import date

from src.database.connection import database_connection
from src.database.load import copy_rows


def test_population_sql_quality_gate_splits_derives_and_checks():
    source_sha256 = "phase_4_population_fixture"

    with database_connection(
        application_name="capstone_phase_4_test"
    ) as connection:
        copy_rows(
            connection,
            schema="raw",
            table="afs_population",
            columns=(
                "plr_id_source",
                "population_total_source",
                "population_65_79_source",
                "population_80plus_source",
                "reference_code_source",
                "reference_date",
                "publication_date",
                "source_path",
                "source_sha256",
                "source_url",
                "publisher",
            ),
            rows=[
                (
                    "A", "100", "15", "5", "209912",
                    date(2099, 12, 31), date(2100, 1, 2),
                    "fixture.csv", source_sha256, "fixture", "fixture",
                ),
                (
                    "B", "200", "20", "10", "209912",
                    date(2099, 12, 31), date(2100, 1, 2),
                    "fixture.csv", source_sha256, "fixture", "fixture",
                ),
                (
                    "C", None, None, None, "209912",
                    date(2099, 12, 31), date(2100, 1, 2),
                    "fixture.csv", source_sha256, "fixture", "fixture",
                ),
            ],
        )

        summary = connection.execute(
            '''
            SELECT
                result.source_row_count,
                result.accepted_row_count,
                result.rejected_row_count
            FROM normalized.refresh_plr_population(%s, %s)
                AS result
            ''',
            (source_sha256, 3),
        ).fetchone()

        assert summary == (3, 2, 1)

        accepted = connection.execute(
            '''
            SELECT
                accepted.plr_id,
                accepted.population_65plus,
                accepted.share_65plus
            FROM normalized.plr_population_65plus AS accepted
            WHERE accepted.source_sha256 = %s
            ORDER BY accepted.plr_id
            ''',
            (source_sha256,),
        ).fetchall()

        assert accepted[0] == ("A", 20, 0.20)
        assert accepted[1] == ("B", 30, 0.15)

        rejected = connection.execute(
            '''
            SELECT
                rejected.plr_id,
                rejected.rejection_reason
            FROM normalized.plr_population_rejected AS rejected
            WHERE rejected.source_sha256 = %s
            ''',
            (source_sha256,),
        ).fetchall()

        assert rejected == [
            ("C", "missing_population_total")
        ]

        quality = connection.execute(
            '''
            SELECT
                result.passed,
                result.source_row_count,
                result.accepted_row_count,
                result.rejected_row_count,
                result.accepted_rejected_overlap,
                result.rejection_reasons
            FROM normalized.check_population_quality(%s)
                AS result
            ''',
            (source_sha256,),
        ).fetchone()

        assert quality[0] is True
        assert quality[1] == 3
        assert quality[2] == 2
        assert quality[3] == 1
        assert quality[4] == 0
        assert quality[5] == {
            "missing_population_total": 1
        }

        connection.rollback()
