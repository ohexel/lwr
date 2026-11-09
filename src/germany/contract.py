"""Source contracts for the Germany layer.

Every number here was observed on the delivered source files on 2026-11-09
(see docs/data-sources.md). A changed checksum or count is a contract
violation, not something to adapt to silently.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path


GERMANY_RAW_ROOT = Path("data/raw/germany")


@dataclass(frozen=True)
class Vg250Contract:
    vintage: date
    source_url: str
    archive_path: Path
    archive_size_bytes: int
    archive_sha256: str
    geopackage_member: str
    source_crs_epsg: int
    publisher: str
    license: str
    attribution: str
    # level -> (all delivered features, land features with GF = 4)
    feature_counts: dict[str, tuple[int, int]]

    @property
    def layers(self) -> dict[str, str]:
        return {level: f"vg250_{level}" for level in self.feature_counts}


VG250_CONTRACT = Vg250Contract(
    vintage=date(2022, 12, 31),
    source_url=(
        "https://daten.gdz.bkg.bund.de/produkte/vg/"
        "vg250_ebenen_1231/2022/vg250_12-31.utm32s.gpkg.ebenen.zip"
    ),
    archive_path=(
        GERMANY_RAW_ROOT
        / "vg250/2022-12-31/vg250_12-31.utm32s.gpkg.ebenen.zip"
    ),
    archive_size_bytes=72_214_199,
    archive_sha256=(
        "4a4d9590cac21028c3f120172d1ab3b026ac0a8b202d0f168e1304383ae44e59"
    ),
    geopackage_member=(
        "vg250_12-31.utm32s.gpkg.ebenen/vg250_ebenen_1231/DE_VG250.gpkg"
    ),
    source_crs_epsg=25832,
    publisher="Bundesamt für Kartographie und Geodäsie (BKG)",
    license="dl-de/by-2-0",
    attribution="© GeoBasis-DE / BKG 2022 (Daten verändert)",
    feature_counts={
        "sta": (11, 1),
        "lan": (35, 16),
        "krs": (430, 400),
        "gem": (11_119, 10_990),
    },
)


@dataclass(frozen=True)
class ZensusExtract:
    regional_variable: str
    level: str
    region_count: int
    csv_sha256: str

    def csv_path(self, table_code: str) -> Path:
        return (
            GERMANY_RAW_ROOT
            / "zensus2022"
            / table_code
            / f"{table_code}_{self.regional_variable}_flat.csv"
        )


@dataclass(frozen=True)
class ZensusContract:
    api_root: str
    table_code: str
    statistic_code: str
    reference_date: date
    territorial_status: date
    age_variable: str
    age_65plus_code: str
    age_class_codes: tuple[str, ...]
    publisher: str
    license: str
    attribution: str
    token_environment_variable: str
    extracts: tuple[ZensusExtract, ...]

    @property
    def tablefile_url(self) -> str:
        return f"{self.api_root}/data/tablefile"


ZENSUS_CONTRACT = ZensusContract(
    api_root="https://ergebnisse.zensus2022.de/api/rest/2020",
    table_code="1000A-1003",
    statistic_code="1000A",
    reference_date=date(2022, 5, 15),
    territorial_status=date(2022, 5, 15),
    age_variable="ALTKL1",
    age_65plus_code="ALT065BXXX",
    age_class_codes=(
        "ALT000B017",
        "ALT018B029",
        "ALT030B049",
        "ALT050B064",
        "ALT065BXXX",
    ),
    publisher="Statistische Ämter des Bundes und der Länder",
    license="dl-de/by-2-0",
    attribution=(
        "© Statistische Ämter des Bundes und der Länder, Deutschland, "
        "2026. Lizenziert unter der Datenlizenz Deutschland - "
        "Namensnennung - Version 2.0"
    ),
    token_environment_variable="ZENSUS2022_TOKEN",
    extracts=(
        ZensusExtract(
            regional_variable="GEOBL1",
            level="lan",
            region_count=16,
            csv_sha256=(
                "83b6ea9532e74e4c27327e9d6544e4ab"
                "d48923dc1ae11563225d6acd312674f3"
            ),
        ),
        ZensusExtract(
            regional_variable="GEOLK4",
            level="krs",
            region_count=400,
            csv_sha256=(
                "71946b5fcc69a3e29d5732e33231badd"
                "7a19a7922e7d9bc39efb82d2e868f35f"
            ),
        ),
        ZensusExtract(
            regional_variable="GEOGM4",
            level="gem",
            region_count=10_786,
            csv_sha256=(
                "e3489be7b6c1d432f155788f9c64ea84"
                "e3f94e535891fa2582bbd81c5c681d4a"
            ),
        ),
    ),
)
