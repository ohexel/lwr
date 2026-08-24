import pytest

from src.database.connection import database_connection


def test_apparent_temperature_shade_formula_fixture() -> None:
    with database_connection(
        application_name="capstone_apparent_temperature_formula_test"
    ) as connection:
        result = connection.execute(
            """
            SELECT normalized.calculate_apparent_temperature_shade_c(
                30.0::DOUBLE PRECISION,
                70.0::DOUBLE PRECISION,
                SQRT(
                    POWER(3.0::DOUBLE PRECISION, 2)
                    + POWER(4.0::DOUBLE PRECISION, 2)
                )
            )
            """
        ).fetchone()

    assert result is not None
    assert float(result[0]) == pytest.approx(
        32.26833429149539,
        abs=1e-9,
    )
