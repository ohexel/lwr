-- Germany layer, indicators: a theme-independent catalogue and value table,
-- and the first source loaded into it (BA Gemeindedaten, ADR 019).
-- Additive and idempotent: only de_-prefixed objects; Berlin objects are
-- never touched. Requires normalized_schema.sql (hierarchy) first.

-- ---------------------------------------------------------------------------
-- raw.de_ba_gemeindedaten: the two data sheets of the BA xlsx, as delivered.
-- One row per region row. Values stay text: '*' is the BA's sign for a
-- suppressed value (Geheimhaltung). Regions are on the territorial status of
-- the reference date, not on the geography version of the hierarchy.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.de_ba_gemeindedaten (
    source_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_name text NOT NULL,
    sheet_row integer NOT NULL,
    source_level text NOT NULL
        CHECK (source_level IN ('gem', 'krs', 'rb', 'lan', 'sta', 'unassigned')),
    source_key text NOT NULL,
    source_name text NOT NULL,
    column_01 text NOT NULL,
    column_02 text NOT NULL,
    column_03 text NOT NULL,
    column_04 text NOT NULL,
    column_05 text NOT NULL,
    column_06 text NOT NULL,
    column_07 text NOT NULL,
    column_08 text NOT NULL,
    column_09 text NOT NULL,
    column_10 text NOT NULL,
    column_11 text NOT NULL,
    column_12 text NOT NULL,
    column_13 text NOT NULL,
    reference_date date NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    publisher text NOT NULL,
    license text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    UNIQUE (reference_date, sheet_name, source_key)
);

COMMENT ON TABLE raw.de_ba_gemeindedaten IS
    'Statistik der Bundesagentur für Arbeit, Gemeindedaten der '
    'sozialversicherungspflichtig Beschäftigten nach Wohn- und Arbeitsort '
    '(product 229), sheets Gemeindedaten and Länder, as delivered. column_NN '
    'is the BA column number. Germany layer.';

-- ---------------------------------------------------------------------------
-- Indicator catalogue: one row per source and per indicator. Filled from the
-- source contracts in Python (src/germany/indicators.py).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_indicator_source (
    source_code text PRIMARY KEY,
    title text NOT NULL,
    publisher text NOT NULL,
    homepage text NOT NULL,
    citation text NOT NULL,
    terms text NOT NULL,
    update_cycle text NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized.de_indicator (
    indicator_code text PRIMARY KEY CHECK (indicator_code ~ '^[a-z0-9_]+$'),
    source_code text NOT NULL REFERENCES normalized.de_indicator_source,
    label_de text NOT NULL,
    unit text NOT NULL,
    source_column integer,
    is_additive boolean NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indicator values on the hierarchy of one geography version. Every region
-- of every level gets a row per indicator and reference date, so a missing
-- value is explicit: value_status says why value is NULL. NULL never means 0.
--   value         the published number
--   suppressed    published as suppressed by the source (BA: '*')
--   not_reported  the source has no row for this region (status_reason says
--                 why, e.g. a Gemeinde merged after the geography version)
-- region_key: Land key, Kreis key, Gemeinde ARS, or 'DG' for Germany.
-- source_key: the key in the source (BA: 8-digit AGS for Gemeinden).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_indicator_value (
    geography_version text NOT NULL,
    level text NOT NULL CHECK (level IN ('sta', 'lan', 'krs', 'gem')),
    region_key text NOT NULL,
    indicator_code text NOT NULL REFERENCES normalized.de_indicator,
    reference_date date NOT NULL,
    value double precision CHECK (value >= 0),
    value_status text NOT NULL
        CHECK (value_status IN ('value', 'suppressed', 'not_reported')),
    status_reason text,
    source_key text,
    source_value text,
    source_sha256 text,
    PRIMARY KEY (geography_version, level, region_key, indicator_code, reference_date),
    CHECK ((value_status = 'value') = (value IS NOT NULL)),
    CHECK ((value_status = 'not_reported') = (status_reason IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS de_indicator_value_vintage_idx
    ON normalized.de_indicator_value (indicator_code, reference_date);

-- ---------------------------------------------------------------------------
-- BA rows in long form, on the BA's own territorial status: one row per
-- region row and indicator. Used by the refresh and by the source checks.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.de_ba_long(p_reference_date date)
RETURNS TABLE(
    sheet_name text,
    source_level text,
    source_key text,
    source_name text,
    indicator_code text,
    is_additive boolean,
    source_value text,
    value bigint,
    source_sha256 text
)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        r.sheet_name,
        r.source_level,
        r.source_key,
        r.source_name,
        i.indicator_code,
        i.is_additive,
        v.source_value,
        CASE WHEN v.source_value ~ '^[0-9]+$' THEN v.source_value::bigint END,
        r.source_sha256
    FROM raw.de_ba_gemeindedaten AS r
    CROSS JOIN normalized.de_indicator AS i
    CROSS JOIN LATERAL (
        SELECT (ARRAY[
            r.column_01, r.column_02, r.column_03, r.column_04, r.column_05,
            r.column_06, r.column_07, r.column_08, r.column_09, r.column_10,
            r.column_11, r.column_12, r.column_13
        ])[i.source_column] AS source_value
    ) AS v
    WHERE r.reference_date = p_reference_date
      AND i.source_code = 'ba_gemeindedaten'
$$;

-- ---------------------------------------------------------------------------
-- Refresh: BA values onto the hierarchy of one geography version.
--   Gemeinde: BA Gemeinde row with the same 8-digit AGS.
--   Kreis:    BA Kreis row; a Kreis with one Gemeinde (kreisfreie Stadt,
--             Berlin, Hamburg) has no Kreis row in the file, its Gemeinde row
--             is the Kreis value.
--   Land:     BA Land row (sheet Gemeindedaten; identical in sheet Länder).
--   Germany:  row "Deutschland" of sheet Länder.
-- The BA's own Kreis and Land values are served, not sums of Gemeinden: they
-- include the values suppressed at Gemeinde level (BA, Erläuterungen).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.refresh_de_ba_indicators(
    p_reference_date date,
    p_geography_version text
) RETURNS TABLE(
    level text,
    value_status text,
    row_count bigint
)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM raw.de_ba_gemeindedaten WHERE reference_date = p_reference_date
    ) THEN
        RAISE EXCEPTION 'No raw BA rows for reference_date=%', p_reference_date;
    END IF;
    IF EXISTS (
        SELECT 1 FROM normalized.de_ba_long(p_reference_date) AS l
        WHERE l.value IS NULL AND l.source_value <> '*'
    ) THEN
        RAISE EXCEPTION 'BA values other than digits or * for %', p_reference_date;
    END IF;

    DELETE FROM normalized.de_indicator_value AS d
    USING normalized.de_indicator AS i
    WHERE d.indicator_code = i.indicator_code
      AND i.source_code = 'ba_gemeindedaten'
      AND d.reference_date = p_reference_date
      AND d.geography_version = p_geography_version;

    INSERT INTO normalized.de_indicator_value (
        geography_version, level, region_key, indicator_code, reference_date,
        value, value_status, status_reason, source_key, source_value,
        source_sha256
    )
    WITH ba AS (
        SELECT * FROM normalized.de_ba_long(p_reference_date)
    ),
    -- Source row per region of the hierarchy (NULL: the BA has no row).
    match AS (
        SELECT 'gem'::text AS level, g.ars AS region_key, g.ags AS source_key,
               'gem'::text AS source_level, 'Gemeindedaten'::text AS sheet_name,
               CASE WHEN g.is_unincorporated THEN 'no_source_record_unincorporated'
                    ELSE 'no_source_record' END AS reason_if_missing
        FROM normalized.de_gemeinde AS g
        WHERE g.geography_version = p_geography_version
        UNION ALL
        SELECT 'krs', k.kreis_key,
               COALESCE(kreis_row.source_key, single.source_key),
               CASE WHEN kreis_row.source_key IS NULL THEN 'gem' ELSE 'krs' END,
               'Gemeindedaten', 'no_source_record'
        FROM normalized.de_kreis AS k
        LEFT JOIN raw.de_ba_gemeindedaten AS kreis_row
          ON kreis_row.reference_date = p_reference_date
         AND kreis_row.sheet_name = 'Gemeindedaten'
         AND kreis_row.source_level = 'krs'
         AND kreis_row.source_key = k.kreis_key
        LEFT JOIN (
            SELECT left(r.source_key, 5) AS kreis_key, MIN(r.source_key) AS source_key
            FROM raw.de_ba_gemeindedaten AS r
            WHERE r.reference_date = p_reference_date
              AND r.sheet_name = 'Gemeindedaten'
              AND r.source_level = 'gem'
            GROUP BY left(r.source_key, 5)
            HAVING COUNT(*) = 1
        ) AS single ON single.kreis_key = k.kreis_key
        WHERE k.geography_version = p_geography_version
        UNION ALL
        SELECT 'lan', l.land_key, l.land_key, 'lan', 'Gemeindedaten', 'no_source_record'
        FROM normalized.de_land AS l
        WHERE l.geography_version = p_geography_version
        UNION ALL
        SELECT 'sta', 'DG', 'DG', 'sta', 'Länder', 'no_source_record'
    )
    SELECT
        p_geography_version,
        m.level,
        m.region_key,
        i.indicator_code,
        p_reference_date,
        b.value::double precision,
        CASE
            WHEN b.source_key IS NULL THEN 'not_reported'
            WHEN b.value IS NULL THEN 'suppressed'
            ELSE 'value'
        END,
        CASE WHEN b.source_key IS NULL THEN m.reason_if_missing END,
        b.source_key,
        b.source_value,
        b.source_sha256
    FROM match AS m
    CROSS JOIN normalized.de_indicator AS i
    LEFT JOIN ba AS b
      ON b.sheet_name = m.sheet_name
     AND b.source_level = m.source_level
     AND b.source_key = m.source_key
     AND b.indicator_code = i.indicator_code
    WHERE i.source_code = 'ba_gemeindedaten';

    RETURN QUERY
    SELECT d.level, d.value_status, COUNT(*)
    FROM normalized.de_indicator_value AS d
    JOIN normalized.de_indicator AS i USING (indicator_code)
    WHERE i.source_code = 'ba_gemeindedaten'
      AND d.reference_date = p_reference_date
      AND d.geography_version = p_geography_version
    GROUP BY d.level, d.value_status
    ORDER BY d.level, d.value_status;
END;
$$;

-- ---------------------------------------------------------------------------
-- Check: key coverage between the BA file and the hierarchy.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_ba_key_coverage(
    p_reference_date date,
    p_geography_version text
) RETURNS TABLE(
    issue text,
    level text,
    region_key text,
    region_name text
)
    LANGUAGE sql STABLE
    AS $$
    SELECT 'source_without_geography', r.source_level, r.source_key, r.source_name
    FROM raw.de_ba_gemeindedaten AS r
    WHERE r.reference_date = p_reference_date
      AND r.sheet_name = 'Gemeindedaten'
      AND (
          (r.source_level = 'gem' AND NOT EXISTS (
              SELECT 1 FROM normalized.de_gemeinde AS g
              WHERE g.geography_version = p_geography_version AND g.ags = r.source_key))
       OR (r.source_level = 'krs' AND NOT EXISTS (
              SELECT 1 FROM normalized.de_kreis AS k
              WHERE k.geography_version = p_geography_version AND k.kreis_key = r.source_key))
       OR (r.source_level = 'lan' AND NOT EXISTS (
              SELECT 1 FROM normalized.de_land AS l
              WHERE l.geography_version = p_geography_version AND l.land_key = r.source_key))
      )
    UNION ALL
    SELECT d.status_reason, d.level,
           CASE WHEN d.level = 'gem' THEN g.ags ELSE d.region_key END,
           COALESCE(g.gemeinde_name, k.kreis_name, l.land_name, 'Deutschland')
    FROM normalized.de_indicator_value AS d
    LEFT JOIN normalized.de_gemeinde AS g
      ON d.level = 'gem' AND g.geography_version = d.geography_version AND g.ars = d.region_key
    LEFT JOIN normalized.de_kreis AS k
      ON d.level = 'krs' AND k.geography_version = d.geography_version AND k.kreis_key = d.region_key
    LEFT JOIN normalized.de_land AS l
      ON d.level = 'lan' AND l.geography_version = d.geography_version AND l.land_key = d.region_key
    WHERE d.reference_date = p_reference_date
      AND d.geography_version = p_geography_version
      AND d.indicator_code = 'employees_residence'
      AND d.value_status = 'not_reported'
    ORDER BY 1, 2, 3
$$;

-- ---------------------------------------------------------------------------
-- Check: sums against the BA's published totals, on the BA's territorial
-- status, for additive indicators only.
--   gem_to_krs: Σ Gemeinden of a Kreis vs the Kreis row. Exact when no
--               Gemeinde value is suppressed; otherwise the published value
--               must be at least the sum of the visible values.
--   krs_to_lan: Σ Kreise (Kreis rows plus single-Gemeinde Kreise) vs Land.
--   lan_to_sta: Σ Länder plus the unassigned row vs "Deutschland".
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_ba_source_totals(
    p_reference_date date
) RETURNS TABLE(
    comparison text,
    region_key text,
    indicator_code text,
    published bigint,
    summed bigint,
    suppressed_parts bigint
)
    LANGUAGE sql STABLE
    AS $$
    WITH l AS MATERIALIZED (
        SELECT * FROM normalized.de_ba_long(p_reference_date) WHERE is_additive
    ),
    kreis_rows AS (
        SELECT * FROM l WHERE sheet_name = 'Gemeindedaten' AND source_level = 'krs'
    ),
    -- Gemeinde rows of Kreise without a Kreis row (kreisfreie Städte).
    -- NOT IN over the small key list runs as a hashed lookup.
    single_gemeinde_kreise AS (
        SELECT g.* FROM l AS g
        WHERE g.sheet_name = 'Gemeindedaten' AND g.source_level = 'gem'
          AND left(g.source_key, 5) NOT IN (
              SELECT r.source_key FROM raw.de_ba_gemeindedaten AS r
              WHERE r.reference_date = p_reference_date
                AND r.sheet_name = 'Gemeindedaten'
                AND r.source_level = 'krs')
    ),
    gem_to_krs AS (
        SELECT 'gem_to_krs'::text, k.source_key, k.indicator_code, k.value,
               COALESCE(SUM(g.value), 0)::bigint,
               COUNT(*) FILTER (WHERE g.value IS NULL)
        FROM kreis_rows AS k
        JOIN l AS g
          ON g.sheet_name = 'Gemeindedaten' AND g.source_level = 'gem'
         AND left(g.source_key, 5) = k.source_key
         AND g.indicator_code = k.indicator_code
        GROUP BY k.source_key, k.indicator_code, k.value
    ),
    krs_to_lan AS (
        SELECT 'krs_to_lan'::text, land.source_key, land.indicator_code, land.value,
               SUM(part.value)::bigint,
               COUNT(*) FILTER (WHERE part.value IS NULL)
        FROM l AS land
        JOIN (
            SELECT source_key, indicator_code, value FROM kreis_rows
            UNION ALL
            SELECT left(source_key, 5), indicator_code, value FROM single_gemeinde_kreise
        ) AS part
          ON left(part.source_key, 2) = land.source_key
         AND part.indicator_code = land.indicator_code
        WHERE land.sheet_name = 'Gemeindedaten' AND land.source_level = 'lan'
        GROUP BY land.source_key, land.indicator_code, land.value
    ),
    lan_to_sta AS (
        SELECT 'lan_to_sta'::text, 'DG'::text, sta.indicator_code, sta.value,
               SUM(part.value)::bigint,
               COUNT(*) FILTER (WHERE part.value IS NULL)
        FROM l AS sta
        JOIN l AS part
          ON part.sheet_name = 'Länder'
         AND part.source_level IN ('lan', 'unassigned')
         AND part.indicator_code = sta.indicator_code
        WHERE sta.sheet_name = 'Länder' AND sta.source_level = 'sta'
        GROUP BY sta.indicator_code, sta.value
    )
    SELECT * FROM gem_to_krs
    UNION ALL SELECT * FROM krs_to_lan
    UNION ALL SELECT * FROM lan_to_sta
$$;

-- ---------------------------------------------------------------------------
-- Check: identities between the columns of one BA row (all levels).
-- Exact: Wohnort = Männer + Frauen. Bounds: Deutsche + Ausländer do not
-- exceed Wohnort (stateless persons are in neither); commuters do not exceed
-- the employees of their side minus those working where they live (persons
-- whose other place is unknown are not counted as commuters). A rule is
-- skipped for a row where one of its values is suppressed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_ba_identities(
    p_reference_date date
) RETURNS TABLE(
    rule text,
    sheet_name text,
    source_key text,
    source_name text
)
    LANGUAGE sql STABLE
    AS $$
    WITH w AS (
        SELECT
            l.sheet_name, l.source_key, MIN(l.source_name) AS source_name,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence') AS res,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence_men') AS men,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence_women') AS women,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence_german') AS german,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence_foreign') AS foreign_,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_workplace') AS work,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'employees_residence_equals_workplace') AS same,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'commuters_in') AS c_in,
            MAX(l.value) FILTER (WHERE l.indicator_code = 'commuters_out') AS c_out
        FROM normalized.de_ba_long(p_reference_date) AS l
        GROUP BY l.sheet_name, l.source_key
    )
    SELECT 'residence = men + women', sheet_name, source_key, source_name
    FROM w WHERE res <> men + women
    UNION ALL
    SELECT 'german + foreign <= residence', sheet_name, source_key, source_name
    FROM w WHERE german + foreign_ > res
    UNION ALL
    SELECT 'commuters_in <= workplace - same', sheet_name, source_key, source_name
    FROM w WHERE c_in > work - same
    UNION ALL
    SELECT 'commuters_out <= residence - same', sheet_name, source_key, source_name
    FROM w WHERE c_out > res - same
$$;

-- ---------------------------------------------------------------------------
-- Serving: the newest reference date of the BA source on the newest
-- geography version, one row per region and level, with the measures the
-- map shows. jobs_ratio (employees at place of work / employees at place of
-- residence) is this project's own calculation from two published values.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW normalized.de_current_indicator_vintage AS
SELECT DISTINCT ON (i.source_code)
    i.source_code, d.reference_date, d.geography_version
FROM normalized.de_indicator_value AS d
JOIN normalized.de_indicator AS i USING (indicator_code)
ORDER BY i.source_code, d.reference_date DESC, d.geography_version DESC;

CREATE OR REPLACE VIEW analytical.de_current_employment AS
WITH vintage AS (
    SELECT reference_date, geography_version
    FROM normalized.de_current_indicator_vintage
    WHERE source_code = 'ba_gemeindedaten'
),
wide AS (
    SELECT
        d.level,
        d.region_key,
        d.reference_date,
        d.geography_version,
        MAX(d.value) FILTER (WHERE d.indicator_code = 'employees_workplace') AS employees_workplace,
        MAX(d.value_status) FILTER (WHERE d.indicator_code = 'employees_workplace') AS employees_workplace_status,
        MAX(d.value) FILTER (WHERE d.indicator_code = 'employees_residence') AS employees_residence,
        MAX(d.value_status) FILTER (WHERE d.indicator_code = 'employees_residence') AS employees_residence_status,
        MAX(d.value) FILTER (WHERE d.indicator_code = 'commuters_in') AS commuters_in,
        MAX(d.value_status) FILTER (WHERE d.indicator_code = 'commuters_in') AS commuters_in_status,
        MAX(d.value) FILTER (WHERE d.indicator_code = 'commuters_out') AS commuters_out,
        MAX(d.value_status) FILTER (WHERE d.indicator_code = 'commuters_out') AS commuters_out_status,
        MAX(d.value) FILTER (WHERE d.indicator_code = 'establishments') AS establishments,
        MAX(d.value_status) FILTER (WHERE d.indicator_code = 'establishments') AS establishments_status,
        MAX(d.status_reason) FILTER (WHERE d.indicator_code = 'employees_residence') AS not_reported_reason
    FROM normalized.de_indicator_value AS d
    JOIN vintage AS v
      ON v.reference_date = d.reference_date
     AND v.geography_version = d.geography_version
    WHERE d.indicator_code IN (
        'employees_workplace', 'employees_residence', 'commuters_in',
        'commuters_out', 'establishments'
    )
    GROUP BY d.level, d.region_key, d.reference_date, d.geography_version
)
SELECT
    w.level,
    w.region_key,
    COALESCE(g.gemeinde_name, k.kreis_name, l.land_name, 'Deutschland') AS region_name,
    COALESCE(g.kreis_key, CASE WHEN w.level = 'krs' THEN w.region_key END) AS kreis_key,
    COALESCE(g.land_key, k.land_key, CASE WHEN w.level = 'lan' THEN w.region_key END) AS land_key,
    w.reference_date,
    w.geography_version,
    w.employees_workplace,
    w.employees_workplace_status,
    w.employees_residence,
    w.employees_residence_status,
    w.commuters_in,
    w.commuters_in_status,
    w.commuters_out,
    w.commuters_out_status,
    w.establishments,
    w.establishments_status,
    w.not_reported_reason,
    CASE
        WHEN w.employees_workplace_status = 'value'
         AND w.employees_residence_status = 'value'
         AND w.employees_residence > 0
        THEN w.employees_workplace / w.employees_residence
    END AS jobs_ratio,
    CASE
        WHEN 'not_reported' IN (w.employees_workplace_status, w.employees_residence_status)
            THEN 'not_reported'
        WHEN 'suppressed' IN (w.employees_workplace_status, w.employees_residence_status)
            THEN 'suppressed'
        WHEN w.employees_residence = 0 THEN 'not_computable'
        ELSE 'value'
    END AS jobs_ratio_status
FROM wide AS w
LEFT JOIN normalized.de_gemeinde AS g
  ON w.level = 'gem' AND g.geography_version = w.geography_version AND g.ars = w.region_key
LEFT JOIN normalized.de_kreis AS k
  ON w.level = 'krs' AND k.geography_version = w.geography_version AND k.kreis_key = w.region_key
LEFT JOIN normalized.de_land AS l
  ON w.level = 'lan' AND l.geography_version = w.geography_version AND l.land_key = w.region_key;

COMMENT ON VIEW analytical.de_current_employment IS
    'BA Gemeindedaten, newest reference date, per Gemeinde/Kreis/Land/Germany: '
    'employees at place of work and of residence, commuters, establishments '
    '(published values with status) and jobs_ratio = workplace / residence '
    '(own calculation). Germany layer.';
