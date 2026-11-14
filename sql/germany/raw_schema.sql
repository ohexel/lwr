-- Germany layer: additive, idempotent DDL.
--
-- This file never alters, drops or truncates a Berlin object. It only creates
-- de_-prefixed relations inside the existing raw / normalized / analytical
-- schemas, so it can be applied repeatedly to an installed Berlin database.

-- ---------------------------------------------------------------------------
-- raw.de_vg250: BKG VG250 administrative units, as delivered.
-- One row per delivered feature of the levels Staat, Land, Kreis, Gemeinde.
-- Water parts (GF = 2) are retained here; normalization filters to GF = 4.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.de_vg250 (
    source_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level text NOT NULL
        CHECK (level IN ('sta', 'lan', 'krs', 'gem')),
    objid text,
    beginn text,
    ade smallint,
    gf smallint,
    bsg smallint,
    ars text,
    ags text,
    sdv_ars text,
    gen text,
    bez text,
    ibz smallint,
    bem text,
    nbd text,
    sn_l text,
    sn_r text,
    sn_k text,
    sn_v1 text,
    sn_v2 text,
    sn_g text,
    fk_s3 text,
    nuts text,
    ars_0 text,
    ags_0 text,
    wsk text,
    dlm_id text,
    geometry_source public.geometry(MultiPolygon, 25832),
    vintage date NOT NULL,
    source_layer text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    publisher text NOT NULL,
    license text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX IF NOT EXISTS de_vg250_source_level_idx
    ON raw.de_vg250 (source_sha256, level);

COMMENT ON TABLE raw.de_vg250 IS
    'BKG VG250 (dl-de/by-2-0) Staat/Land/Kreis/Gemeinde features as '
    'delivered, EPSG:25832, all GF values. Germany layer.';

-- ---------------------------------------------------------------------------
-- raw.de_zensus2022_population: Zensus 2022 table 1000A-1003 as delivered in the
-- API's flat CSV ("ffcsv") format. One file per requested regional variable;
-- each file also carries the Germany (GEODL1) rows. Values stay text: the
-- source uses sign characters (e.g. '-') that normalization interprets.
-- Named for its use: only the total population is read from it. The age
-- classes stay as delivered (see ADR 017).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.de_zensus2022_population (
    source_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_code text NOT NULL,
    requested_regional_variable text NOT NULL,
    statistics_code text,
    statistics_label text,
    time_code text,
    time_label text,
    time_value text,
    variable_1_code text,
    variable_1_label text,
    variable_1_attribute_code text,
    variable_1_attribute_label text,
    variable_2_code text,
    variable_2_label text,
    variable_2_attribute_code text,
    variable_2_attribute_label text,
    value text,
    value_unit text,
    value_variable_code text,
    value_variable_label text,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    publisher text NOT NULL,
    license text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX IF NOT EXISTS de_zensus2022_population_source_idx
    ON raw.de_zensus2022_population (source_sha256);

COMMENT ON TABLE raw.de_zensus2022_population IS
    'Zensus 2022 table 1000A-1003 (Personen: Alter, 5 Altersklassen), '
    'Gebietsstand 15.05.2022, as delivered (flat CSV). The pipeline uses the '
    'total over all age classes. dl-de/by-2-0. Germany layer.';
