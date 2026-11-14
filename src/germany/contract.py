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


# ---------------------------------------------------------------------------
# Sector data (third theme, ADR 020): BA Regionalreport per Kreis and BA
# Beschäftigte nach Wirtschaftszweigen per Land. Both quarterly, employees
# subject to social insurance at place of work.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BaRegionalreportContract:
    source_code: str
    title: str
    product_id: int
    url_template: str
    citation: str
    # Table 2.3, column 1 after whitespace collapse, in delivered order, with
    # the indicator code each row feeds (None: kept in raw, used by checks).
    sector_rows: tuple[tuple[str, str | None, str], ...]
    quarters_per_file: int
    # Kreis keys are those of the hierarchy (400 for VG250 2022).
    probe_kreis_key: str

    def url(self, kreis_key: str, file_date: date) -> str:
        return self.url_template.format(kreis=kreis_key, yyyymm=file_date.strftime("%Y%m"))

    def path(self, kreis_key: str, file_date: date) -> Path:
        stamp = file_date.strftime("%Y%m")
        return (
            GERMANY_RAW_ROOT / "ba_regionalreport" / file_date.isoformat()
            / f"bst-reg-{kreis_key}-0-{stamp}.xlsx"
        )


@dataclass(frozen=True)
class BaWzHeftContract:
    source_code: str
    title: str
    url_template: str
    citation: str
    sheet: str
    # File region code ('01'..'16', 'd') -> region key in the hierarchy.
    regions: dict[str, str]
    value_columns: int

    def url(self, region_code: str, file_date: date) -> str:
        return self.url_template.format(ll=region_code, yyyymm=file_date.strftime("%Y%m"))

    def path(self, region_code: str, file_date: date) -> Path:
        stamp = file_date.strftime("%Y%m")
        return (
            GERMANY_RAW_ROOT / "ba_wz_heft" / file_date.isoformat()
            / f"wz-heft-{region_code}-0-{stamp}.xlsx"
        )


BA_REGIONALREPORT_CONTRACT = BaRegionalreportContract(
    source_code="ba_regionalreport",
    title="Regionalreport über Beschäftigte (Quartalszahlen)",
    product_id=709,
    url_template=(
        "https://statistik.arbeitsagentur.de/Statistikdaten/Detail/{yyyymm}/"
        "iiia6/beschaeftigung-reg-bst-reg/bst-reg-{kreis}-0-{yyyymm}-xlsx.xlsx"
        "?__blob=publicationFile&v=1"
    ),
    citation=(
        "Statistik der Bundesagentur für Arbeit, Tabellen, Regionalreport über "
        "Beschäftigte (Quartalszahlen)"
    ),
    sector_rows=(
        ("Insgesamt", "sector_total", "Insgesamt"),
        ("A", "sector_a", "Land-, Forstwirtschaft und Fischerei"),
        ("B,D,E", "sector_b_d_e", "Bergbau, Energie- und Wasserversorgung, Entsorgungswirtschaft"),
        ("C", "sector_c", "Verarbeitendes Gewerbe"),
        ("dav. 10-15, 18, 21, 31", "sector_c_consumer_goods",
         "Herstellung von überwiegend häuslich konsumierten Gütern (ohne Güter der Metall-, Elektro- und Chemieindustrie)"),
        ("24-30, 32, 33", "sector_c_metal_electrical_steel",
         "Metall- und Elektroindustrie sowie Stahlindustrie"),
        ("16, 17, 19, 20, 22, 23", "sector_c_intermediate_goods",
         "Herstellung von Vorleistungsgütern, insbesondere von chemischen Erzeugnissen und Kunststoffwaren (ohne Güter der Metall- und Elektroindustrie)"),
        ("F", "sector_f", "Baugewerbe"),
        ("G", "sector_g", "Handel, Instandhaltung, Reparatur von Kfz"),
        ("H", "sector_h", "Verkehr und Lagerei"),
        ("I", "sector_i", "Gastgewerbe"),
        ("J", "sector_j", "Information und Kommunikation"),
        ("K", "sector_k", "Erbringung von Finanz- und Versicherungsdienstleistungen"),
        ("L,M", "sector_l_m", "Immobilien, freiberufliche wissenschaftliche und technische Dienstleistungen"),
        ("N", "sector_n", "sonstige wirtschaftliche Dienstleistungen"),
        ("dav. N (ohne ANÜ)", "sector_n_excl_temporary_agency",
         "sonstige wirtschaftliche Dienstleistungen (ohne Arbeitnehmerüberlassung)"),
        ("782.783", "sector_temporary_agency", "Arbeitnehmerüberlassung"),
        ("O, U", "sector_o_u", "Öffentliche Verwaltung, Verteidigung, Sozialversicherung, Exterritoriale Organisationen"),
        ("P", "sector_p", "Erziehung und Unterricht"),
        ("86", "sector_q86", "Gesundheitswesen"),
        ("87.88", "sector_q87_88", "Heime und Sozialwesen"),
        ("R,S,T", "sector_r_s_t", "sonstige Dienstleistungen, Private Haushalte"),
        ("", "sector_unassigned", "keine Zuordnung möglich"),
        ("A", None, "Land-, Forstwirtschaft und Fischerei (Sektoren)"),
        ("B - F", None, "Produzierendes Gewerbe"),
        ("G - U", None, "Dienstleistungsbereich"),
    ),
    quarters_per_file=5,
    probe_kreis_key="01001",
)

BA_WZ_HEFT_CONTRACT = BaWzHeftContract(
    source_code="ba_wz_heft",
    title="Beschäftigte nach Wirtschaftszweigen (WZ 2008) (Quartalszahlen)",
    url_template=(
        "https://statistik.arbeitsagentur.de/Statistikdaten/Detail/{yyyymm}/"
        "iiia6/beschaeftigung-sozbe-wz-heft/wz-heft-{ll}-0-{yyyymm}-xlsx.xlsx"
        "?__blob=publicationFile&v=1"
    ),
    citation=(
        "Statistik der Bundesagentur für Arbeit, Tabellen, Beschäftigte nach "
        "Wirtschaftszweigen (WZ 2008) (Quartalszahlen)"
    ),
    sheet="SVB - Tabelle I",
    regions={**{f"{n:02d}": f"{n:02d}" for n in range(1, 17)}, "d": "DG"},
    value_columns=11,
)

# Newest quarter loaded when no file date is configured (both products were
# online for it on 14 Nov 2026; the next, 30 Jun 2026, is due in January 2027).
SECTOR_LATEST_KNOWN = date(2026, 3, 31)

@dataclass(frozen=True)
class SectorSourceDiscrepancy:
    """A difference in the BA's own published figures, reviewed and documented.
    The check accepts exactly this gap for this Land and quarter; the map
    shows the note for the Kreis."""

    quarter: date
    land_key: str
    kreis_key: str
    kreise_below_land: int
    note_de: str


# Observed on 14 Nov 2026 in the files for 31 Mar 2026: Main-Kinzig-Kreis
# falls from 142,459 to 93,730 employees in one quarter (-34 %), in every
# section and also by place of residence, while Hessen's total (Regionalreport
# table 2.1 and WZ booklet) barely changes; the Kreise of Hessen fall 47,649
# short of the Land. No other Kreis changes by more than 10 %.
SECTOR_SOURCE_DISCREPANCIES: tuple[SectorSourceDiscrepancy, ...] = (
    SectorSourceDiscrepancy(
        quarter=date(2026, 3, 31),
        land_key="06",
        kreis_key="06435",
        kreise_below_land=47_649,
        note_de=(
            "Die BA weist für diesen Kreis zum 31. März 2026 rund ein Drittel weniger "
            "Beschäftigte aus als im Vorquartal, in allen Wirtschaftszweigen, während "
            "die Zahl für Hessen fast gleich bleibt; die Summe der hessischen Kreise "
            "liegt 47.649 unter dem Landeswert. Die Kreiswerte dieses Quartals sind "
            "vermutlich unvollständig."
        ),
    ),
)
# Germany includes employees who cannot be assigned to a Land (Gemeindedaten:
# 2,160 on 30 Jun 2025); the gap may not exceed this share of Germany.
MAX_UNASSIGNED_SHARE = 1e-4

# Manufacturing groups of the Regionalreport as WZ 2008 divisions, so the
# Land values from the WZ booklets can be grouped the same way.
SECTOR_GROUP_DIVISIONS: dict[str, tuple[str, ...]] = {
    "sector_c_consumer_goods": ("10", "11", "12", "13", "14", "15", "18", "21", "31"),
    "sector_c_metal_electrical_steel": ("24", "25", "26", "27", "28", "29", "30", "32", "33"),
    "sector_c_intermediate_goods": ("16", "17", "19", "20", "22", "23"),
}
# The four industries of the Avenir question, published only per Land.
LAND_DIVISIONS: dict[str, tuple[str, str]] = {
    "29": ("wz_29", "Herstellung von Kraftwagen und Kraftwagenteilen"),
    "28": ("wz_28", "Maschinenbau"),
    "20": ("wz_20", "Herstellung von chemischen Erzeugnissen"),
    "241": ("wz_241", "Erzeugung von Roheisen, Stahl und Ferrolegierungen"),
}


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
