"""Source contracts for the Germany layer.

Every number here was observed on the delivered source files on 2026-11-09
(see docs/data-sources.md). A changed checksum or count is a contract
violation, not something to adapt to silently.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path


GERMANY_RAW_ROOT = Path("data/raw/germany")

# Version tag of the normalized Germany geography (VG250 vintage).
GEOGRAPHY_VERSION = "vg250-2022-12-31"


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
    # The measure served by the Germany layer is total population: the row
    # of each region without an age class (empty 2_variable_attribute_code).
    # Destatis publishes official population counts unperturbed; the age
    # classes are perturbed by the Cell Key Method and stay raw-only.
    age_class_codes: tuple[str, ...]
    publisher: str
    license: str
    attribution: str
    token_environment_variable: str
    extracts: tuple[ZensusExtract, ...]

    @property
    def tablefile_url(self) -> str:
        return f"{self.api_root}/data/tablefile"


@dataclass(frozen=True)
class BaVintage:
    """What is known about one published vintage of the BA Gemeindedaten.

    A vintage listed here is verified against its checksum and counts. A
    newer vintage (published yearly) is accepted when it passes the
    structural contract; its counts and key exceptions are then reported by
    the checks for review instead of being compared with pinned values.
    """

    reference_date: date
    sha256: str
    size_bytes: int
    # rows of the sheet "Gemeindedaten" per key length
    gemeinde_rows: int
    kreis_rows: int
    regierungsbezirk_rows: int
    # BA keys (territorial status of the reference date) without a VG250 match
    gemeinden_without_geography: frozenset[str]
    # incorporated VG250 Gemeinden absent from the BA file (merged after 2022)
    geography_without_source: frozenset[str]


@dataclass(frozen=True)
class BaGemeindedatenContract:
    source_code: str
    title: str
    publisher: str
    product_id: int
    url_template: str
    homepage: str
    citation: str
    terms: str
    # Sheet names, in order of use, and the delivered column layout.
    data_sheet: str
    laender_sheet: str
    imprint_sheet: str
    first_data_row: int
    column_numbers: tuple[int, ...]
    # Text of the column-number row (row 8) is 1..13; the header cells below
    # are matched after collapsing whitespace.
    header_fragments: tuple[str, ...]
    unassigned_label: str
    germany_label: str
    land_count: int
    suppressed_sign: str
    vintages: tuple[BaVintage, ...]

    def url(self, reference_date: date) -> str:
        return self.url_template.format(yyyymm=reference_date.strftime("%Y%m"))

    def path(self, reference_date: date) -> Path:
        stamp = reference_date.strftime("%Y%m")
        return (
            GERMANY_RAW_ROOT
            / "ba_gemeindedaten"
            / reference_date.isoformat()
            / f"gemband-dlk-0-{stamp}.xlsx"
        )

    def vintage(self, reference_date: date) -> BaVintage | None:
        for vintage in self.vintages:
            if vintage.reference_date == reference_date:
                return vintage
        return None

    @property
    def latest_known(self) -> date:
        return max(vintage.reference_date for vintage in self.vintages)


# Indicator code, German source label, column number in the BA table.
# Codes are theme-independent keys of normalized.de_indicator.
BA_INDICATORS: tuple[tuple[str, str, int], ...] = (
    ("employees_residence", "Sozialversicherungspflichtig Beschäftigte am Wohnort", 1),
    ("employees_residence_men", "darunter Männer (Wohnort)", 2),
    ("employees_residence_women", "darunter Frauen (Wohnort)", 3),
    ("employees_residence_german", "darunter Deutsche (Wohnort)", 4),
    ("employees_residence_foreign", "darunter Ausländer (Wohnort)", 5),
    ("employees_residence_under_20", "darunter unter 20 Jahre (Wohnort)", 6),
    ("employees_residence_20_to_24", "darunter 20 bis unter 25 Jahre (Wohnort)", 7),
    ("employees_residence_55_and_over", "darunter 55 Jahre und älter (Wohnort)", 8),
    ("employees_workplace", "Sozialversicherungspflichtig Beschäftigte am Arbeitsort", 9),
    ("employees_residence_equals_workplace", "Wohnort gleich Arbeitsort", 10),
    ("commuters_in", "Einpendler", 11),
    ("commuters_out", "Auspendler", 12),
    ("establishments", "Zahl der Betriebe", 13),
)

# Measures whose Gemeinde values add up to the Kreis and Land values. The
# commuter columns and "Wohnort gleich Arbeitsort" depend on the level (a
# commuter between two Gemeinden of one Kreis is not a Kreis commuter).
BA_ADDITIVE_INDICATORS: frozenset[str] = frozenset(
    {
        "employees_residence",
        "employees_residence_men",
        "employees_residence_women",
        "employees_residence_german",
        "employees_residence_foreign",
        "employees_residence_under_20",
        "employees_residence_20_to_24",
        "employees_residence_55_and_over",
        "employees_workplace",
        "establishments",
    }
)

BA_GEMEINDEDATEN_CONTRACT = BaGemeindedatenContract(
    source_code="ba_gemeindedaten",
    title=(
        "Gemeindedaten der sozialversicherungspflichtig Beschäftigten nach "
        "Wohn- und Arbeitsort"
    ),
    publisher="Statistik der Bundesagentur für Arbeit",
    product_id=229,
    url_template=(
        "https://statistik.arbeitsagentur.de/Statistikdaten/Detail/{yyyymm}/"
        "iiia6/beschaeftigung-sozbe-gemband/gemband-dlk-0-{yyyymm}-xlsx.xlsx"
        "?__blob=publicationFile&v=1"
    ),
    homepage="https://statistik.arbeitsagentur.de/",
    citation=(
        "Statistik der Bundesagentur für Arbeit, Tabellen, Gemeindedaten der "
        "sozialversicherungspflichtig Beschäftigten nach Wohn- und Arbeitsort, "
        "Nürnberg"
    ),
    terms=(
        "© Statistik der Bundesagentur für Arbeit. Free use with source "
        "citation; contents must not be altered; own calculations must be "
        "marked; online publications should link to the BA statistics homepage."
    ),
    data_sheet="Gemeindedaten",
    laender_sheet="Länder",
    imprint_sheet="Impressum",
    first_data_row=10,
    column_numbers=tuple(range(1, 14)),
    header_fragments=(
        "Sozialversicherungspflichtig Beschäftigte am",
        "Wohnort",
        "Arbeitsort",
        "Zahl der Betriebe",
    ),
    unassigned_label="keine Angabe/keine Zuordnung möglich",
    germany_label="Deutschland",
    land_count=16,
    suppressed_sign="*",
    vintages=(
        BaVintage(
            reference_date=date(2025, 6, 30),
            sha256="a89513bdd5bfa967f9b81b86910e2e5d04376f82ef56b4e86f2212922a5953ff",
            size_bytes=1_608_751,
            gemeinde_rows=10_752,
            kreis_rows=294,
            regierungsbezirk_rows=20,
            gemeinden_without_geography=frozenset(
                {"07232503", "14522275", "16061119", "16076094"}
            ),
            geography_without_source=frozenset(
                {
                    "01059101", "01059141", "06635005", "07232004", "07232089",
                    "07232096", "13071017", "13071114", "14522450", "14522620",
                    "16061007", "16061028", "16061039", "16061049", "16061065",
                    "16061067", "16061068", "16061077", "16061084", "16061091",
                    "16061096", "16061097", "16061111", "16063028", "16063037",
                    "16064014", "16064055", "16064058", "16064072", "16064073",
                    "16066073", "16067036", "16068043", "16071077", "16076004",
                    "16076026", "16076038", "16076084",
                }
            ),
        ),
    ),
)


ZENSUS_CONTRACT = ZensusContract(
    api_root="https://ergebnisse.zensus2022.de/api/rest/2020",
    table_code="1000A-1003",
    statistic_code="1000A",
    reference_date=date(2022, 5, 15),
    territorial_status=date(2022, 5, 15),
    age_variable="ALTKL1",
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
