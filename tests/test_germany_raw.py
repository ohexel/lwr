"""Germany layer, M1: source contracts, acquisition safety and raw parsing."""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from src.germany import acquire
from src.germany.contract import (
    GERMANY_RAW_ROOT,
    VG250_CONTRACT,
    ZENSUS_CONTRACT,
)
from src.germany.raw_load import (
    ZENSUS_CSV_COLUMNS,
    parse_zensus_flat_csv,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "0123456789abcdef0123456789abcdef"
SHA256 = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_vg250_contract_matches_zensus_territorial_status() -> None:
    # Zensus Gebietsstand 15.05.2022; VG250 31.12.2022 is the first vintage
    # that contains every Zensus Gemeinde key (see docs/data-sources.md).
    assert VG250_CONTRACT.vintage.isoformat() == "2022-12-31"
    assert "/vg250_ebenen_1231/2022/" in VG250_CONTRACT.source_url
    assert ZENSUS_CONTRACT.territorial_status.isoformat() == "2022-05-15"
    assert VG250_CONTRACT.vintage >= ZENSUS_CONTRACT.territorial_status


@pytest.mark.contract
def test_vg250_contract_counts() -> None:
    assert VG250_CONTRACT.feature_counts == {
        "sta": (11, 1),
        "lan": (35, 16),
        "krs": (430, 400),
        "gem": (11_119, 10_990),
    }
    assert VG250_CONTRACT.source_crs_epsg == 25832
    assert VG250_CONTRACT.license == "dl-de/by-2-0"
    assert SHA256.fullmatch(VG250_CONTRACT.archive_sha256)


@pytest.mark.contract
def test_zensus_contract_extracts() -> None:
    assert ZENSUS_CONTRACT.table_code == "1000A-1003"
    assert not hasattr(ZENSUS_CONTRACT, "age_65plus_code")
    assert len(ZENSUS_CONTRACT.age_class_codes) == 5
    assert {
        (extract.regional_variable, extract.region_count)
        for extract in ZENSUS_CONTRACT.extracts
    } == {("GEOBL1", 16), ("GEOLK4", 400), ("GEOGM4", 10_786)}
    for extract in ZENSUS_CONTRACT.extracts:
        assert SHA256.fullmatch(extract.csv_sha256)


@pytest.mark.contract
def test_germany_sources_live_under_ignored_data_directory() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"(?m)^data/$", gitignore)
    assert GERMANY_RAW_ROOT.parts[0] == "data"
    assert VG250_CONTRACT.archive_path.is_relative_to(GERMANY_RAW_ROOT)
    for extract in ZENSUS_CONTRACT.extracts:
        assert extract.csv_path(ZENSUS_CONTRACT.table_code).is_relative_to(
            GERMANY_RAW_ROOT
        )


@pytest.mark.contract
def test_germany_schema_is_additive_and_prefixed() -> None:
    ddl = (PROJECT_ROOT / "sql" / "germany" / "raw_schema.sql").read_text(encoding="utf-8")
    code = "\n".join(
        line.split("--", 1)[0] for line in ddl.splitlines()
    ).upper()
    for forbidden in ("DROP ", "TRUNCATE", "ALTER ", "DELETE ", "UPDATE "):
        assert forbidden not in code
    created = re.findall(
        r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)", code
    )
    assert created
    for name in created:
        assert name.split(".")[-1].startswith("DE_")
    assert "CREATE TABLE " not in code.replace("CREATE TABLE IF NOT EXISTS", "")


# ---------------------------------------------------------------------------
# Token safety: the Zensus API echoes the token in "Username".
# ---------------------------------------------------------------------------
def test_redact_api_payload_removes_nested_credentials() -> None:
    payload = {
        "Status": {"Code": 0, "Content": "ok"},
        "Username": FAKE_TOKEN,
        "Parameter": {"username": FAKE_TOKEN, "password": "secret"},
        "List": [{"Username": FAKE_TOKEN, "Code": "x"}],
    }
    redacted = json.dumps(acquire.redact_api_payload(payload))
    assert FAKE_TOKEN not in redacted
    assert "secret" not in redacted
    assert '"Code": "x"' in redacted


class _FakeResponse:
    def __init__(self, status_code: int, content_type: str, body: bytes):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.content = body

    def json(self):
        return json.loads(self.content)


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def _zip_with_csv(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("1000A-1003_1_de_flat.csv", text)
    return buffer.getvalue()


def test_zensus_error_messages_never_contain_the_token() -> None:
    body = json.dumps(
        {
            "Status": {"Code": 98, "Content": "Fehler"},
            "Username": FAKE_TOKEN,
        }
    ).encode()
    session = _FakeSession(_FakeResponse(200, "application/json", body))
    extract = ZENSUS_CONTRACT.extracts[0]

    with pytest.raises(RuntimeError) as error:
        acquire.download_zensus_extract(session, extract, FAKE_TOKEN)

    assert FAKE_TOKEN not in str(error.value)
    assert "Fehler" in str(error.value)


def test_zensus_token_is_sent_only_as_header() -> None:
    csv_text = ";".join(ZENSUS_CSV_COLUMNS) + "\n"
    session = _FakeSession(
        _FakeResponse(200, "application/zip", _zip_with_csv(csv_text))
    )
    extract = ZENSUS_CONTRACT.extracts[0]

    result = acquire.download_zensus_extract(session, extract, FAKE_TOKEN)

    (call,) = session.calls
    assert result == csv_text.encode()
    assert call["headers"]["username"] == FAKE_TOKEN
    assert FAKE_TOKEN not in call["url"]
    assert FAKE_TOKEN not in json.dumps(call["data"])
    assert call["data"]["regionalvariable"] == extract.regional_variable
    assert call["data"]["format"] == "ffcsv"


def test_missing_zensus_token_explains_registration(monkeypatch) -> None:
    monkeypatch.delenv(ZENSUS_CONTRACT.token_environment_variable, raising=False)
    with pytest.raises(RuntimeError, match="ZENSUS2022_TOKEN"):
        acquire.zensus_token()


# ---------------------------------------------------------------------------
# Raw parsing keeps values as delivered.
# ---------------------------------------------------------------------------
def _flat_csv(*rows: list[str]) -> str:
    header = "﻿" + ";".join(ZENSUS_CSV_COLUMNS)
    return "\n".join([header, *(";".join(row) for row in rows)]) + "\n"


def _row(region: str, age: str, value: str) -> list[str]:
    return [
        "1000A", "Bevölkerung kompakt (Gebietsstand 15.05.2022)", "STAG",
        "Stichtag", "2022-05-15", "GEOGM4", "Gemeinden", region, "Name",
        "ALTKL1", "Alter (5 Altersklassen)", age, "label", value, "Anzahl",
        "PRS018", "Personen",
    ]


def test_parse_zensus_flat_csv_keeps_signs_and_leading_zeroes() -> None:
    text = _flat_csv(
        _row("010010000000", "ALT065BXXX", "17520"),
        _row("072325005130", "ALT065BXXX", "-"),
    )
    rows = list(parse_zensus_flat_csv(text))
    assert len(rows) == 2
    assert rows[0][7] == "010010000000"
    assert rows[0][13] == "17520"
    assert rows[1][13] == "-"


def test_parse_zensus_flat_csv_maps_empty_fields_to_null() -> None:
    row = _row("DG", "", "83237124")
    rows = list(parse_zensus_flat_csv(_flat_csv(row)))
    assert rows[0][11] is None


def test_parse_zensus_flat_csv_rejects_unexpected_header() -> None:
    with pytest.raises(ValueError, match="header"):
        list(parse_zensus_flat_csv("a;b;c\n1;2;3\n"))


def test_parse_zensus_flat_csv_rejects_short_rows() -> None:
    with pytest.raises(ValueError, match="line 2"):
        list(parse_zensus_flat_csv(_flat_csv(["1000A", "x"])))


# ---------------------------------------------------------------------------
# Database (run after `python -m src.germany.acquire` and the raw assets).
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_raw_germany_tables_match_contracts() -> None:
    from src.dagster_pipeline.checks.germany_raw import (
        de_vg250_source_contract,
        de_zensus2022_age_source_contract,
    )

    for check in (de_vg250_source_contract, de_zensus2022_age_source_contract):
        result = check.node_def.compute_fn.decorated_fn()
        assert result.passed, result.metadata
