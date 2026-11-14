-- Germany layer, sectors (third theme, ADR 020): BA Regionalreport per Kreis
-- and BA Beschäftigte nach Wirtschaftszweigen per Land, in the indicator
-- tables of ADR 019. Additive and idempotent: only de_-prefixed objects.
-- Requires normalized_schema.sql and indicator_schema.sql first.

-- ---------------------------------------------------------------------------
-- raw.de_ba_regionalreport: tables 2.3 (sections and manufacturing groups)
-- and 2.1 (totals of the Kreis, its Land, West/East and Germany) of one
-- Regionalreport per Kreis, as delivered: one row per table row and
-- quarter. Each file holds five quarters; a quarter appears in up to five
-- files and the newest file wins. '*' = suppressed by the BA.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.de_ba_regionalreport (
    source_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_date date NOT NULL,
    kreis_key text NOT NULL CHECK (kreis_key ~ '^[0-9]{5}$'),
    table_name text NOT NULL CHECK (table_name IN ('2.1', '2.3')),
    sheet_row integer NOT NULL,
    region_label text NOT NULL,
    row_code text NOT NULL,
    row_label text NOT NULL,
    quarter_date date NOT NULL,
    quarter_label text NOT NULL,
    value text NOT NULL CHECK (value ~ '^([0-9]+|[*])$'),
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    UNIQUE (file_date, kreis_key, table_name, sheet_row, quarter_date)
);

CREATE INDEX IF NOT EXISTS de_ba_regionalreport_quarter_idx
    ON raw.de_ba_regionalreport (table_name, quarter_date, kreis_key);

COMMENT ON TABLE raw.de_ba_regionalreport IS
    'Statistik der Bundesagentur für Arbeit, Regionalreport über Beschäftigte '
    '(Quartalszahlen, product 709), tables 2.1 and 2.3 per Kreis, employees '
    'subject to social insurance at place of work, as delivered. Germany layer.';

-- ---------------------------------------------------------------------------
-- raw.de_ba_wz_heft: sheet "SVB - Tabelle I" of the WZ booklet of one Land or
-- Germany, as delivered: one row per WZ code ('total', 2-digit divisions,
-- 3-digit groups, 'XX' = keine Angabe), column_values = the BA's columns 1-11
-- (1 = Insgesamt).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.de_ba_wz_heft (
    source_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_date date NOT NULL,
    region_key text NOT NULL CHECK (region_key ~ '^([0-9]{2}|DG)$'),
    region_name text NOT NULL,
    sheet_row integer NOT NULL,
    wz_code text NOT NULL CHECK (wz_code ~ '^([0-9]{2,3}|XX|total)$'),
    wz_label text NOT NULL,
    column_values text[] NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    UNIQUE (file_date, region_key, wz_code)
);

COMMENT ON TABLE raw.de_ba_wz_heft IS
    'Statistik der Bundesagentur für Arbeit, Beschäftigte nach '
    'Wirtschaftszweigen (WZ 2008) (Quartalszahlen), sheet SVB - Tabelle I per '
    'Land and Germany, as delivered. Germany layer.';

-- ---------------------------------------------------------------------------
-- Derived indicators that are sums of published ones (own calculations), for
-- example a Land value of a Regionalreport group from the WZ divisions it
-- consists of. Filled from the contracts in Python.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_indicator_sum_component (
    derived_code text NOT NULL REFERENCES normalized.de_indicator,
    component_code text NOT NULL REFERENCES normalized.de_indicator,
    PRIMARY KEY (derived_code, component_code)
);

-- ---------------------------------------------------------------------------
-- Refresh: all quarters of both sources onto the hierarchy.
--   Kreis (ba_regionalreport): table 2.3 of the Kreis file; for a quarter in
--     several files the newest file is used. p_row_indicators maps the
--     position of a row in table 2.3 to its indicator (NULL: raw only).
--   Land and Germany (ba_wz_heft): column 1 (Insgesamt) of the booklet.
-- A source gets rows only for the levels it publishes (ADR 020); within a
-- level every region gets a row per indicator and quarter, with
-- 'not_reported' where no file was loaded.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.refresh_de_ba_sectors(
    p_geography_version text,
    p_row_indicators text[]
) RETURNS TABLE(
    source_code text,
    level text,
    value_status text,
    row_count bigint
)
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM normalized.de_indicator_value AS d
    USING normalized.de_indicator AS i
    WHERE d.indicator_code = i.indicator_code
      AND i.source_code IN ('ba_regionalreport', 'ba_wz_heft')
      AND d.geography_version = p_geography_version;

    INSERT INTO normalized.de_indicator_value (
        geography_version, level, region_key, indicator_code, reference_date,
        value, value_status, status_reason, source_key, source_value,
        source_sha256
    )
    WITH positioned AS (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY r.file_date, r.kreis_key, r.quarter_date
                   ORDER BY r.sheet_row
               ) AS position
        FROM raw.de_ba_regionalreport AS r
        WHERE r.table_name = '2.3'
    ),
    newest AS (
        SELECT DISTINCT ON (p.kreis_key, p.position, p.quarter_date)
               p.kreis_key, p.position, p.quarter_date, p.value, p.source_sha256
        FROM positioned AS p
        ORDER BY p.kreis_key, p.position, p.quarter_date, p.file_date DESC
    ),
    mapped AS (
        SELECT n.*, p_row_indicators[n.position] AS indicator_code
        FROM newest AS n
        WHERE p_row_indicators[n.position] IS NOT NULL
    ),
    quarters AS (SELECT DISTINCT quarter_date FROM mapped)
    SELECT
        p_geography_version, 'krs', k.kreis_key, i.indicator_code, q.quarter_date,
        CASE WHEN m.value ~ '^[0-9]+$' THEN m.value::double precision END,
        CASE WHEN m.value IS NULL THEN 'not_reported'
             WHEN m.value = '*' THEN 'suppressed' ELSE 'value' END,
        CASE WHEN m.value IS NULL THEN 'no_source_file' END,
        CASE WHEN m.value IS NOT NULL THEN k.kreis_key END,
        m.value,
        m.source_sha256
    FROM normalized.de_kreis AS k
    CROSS JOIN quarters AS q
    CROSS JOIN normalized.de_indicator AS i
    LEFT JOIN mapped AS m
      ON m.kreis_key = k.kreis_key
     AND m.quarter_date = q.quarter_date
     AND m.indicator_code = i.indicator_code
    WHERE k.geography_version = p_geography_version
      AND i.source_code = 'ba_regionalreport'
      AND i.source_column IS NOT NULL;

    INSERT INTO normalized.de_indicator_value (
        geography_version, level, region_key, indicator_code, reference_date,
        value, value_status, status_reason, source_key, source_value,
        source_sha256
    )
    WITH regions AS (
        SELECT 'lan'::text AS level, l.land_key AS region_key
        FROM normalized.de_land AS l
        WHERE l.geography_version = p_geography_version
        UNION ALL
        SELECT 'sta', 'DG'
    ),
    booklet AS (
        SELECT w.*,
               CASE w.wz_code WHEN 'total' THEN 'wz_total'
                              WHEN 'XX' THEN 'wz_unassigned'
                              ELSE 'wz_' || w.wz_code END AS indicator_code,
               w.column_values[1] AS value
        FROM raw.de_ba_wz_heft AS w
    ),
    quarters AS (SELECT DISTINCT file_date FROM raw.de_ba_wz_heft)
    SELECT
        p_geography_version, r.level, r.region_key, i.indicator_code, q.file_date,
        CASE WHEN b.value ~ '^[0-9]+$' THEN b.value::double precision END,
        CASE WHEN b.value IS NULL THEN 'not_reported'
             WHEN b.value = '*' THEN 'suppressed' ELSE 'value' END,
        CASE WHEN b.value IS NULL THEN 'no_source_row' END,
        CASE WHEN b.value IS NOT NULL THEN r.region_key END,
        b.value,
        b.source_sha256
    FROM regions AS r
    CROSS JOIN quarters AS q
    CROSS JOIN normalized.de_indicator AS i
    LEFT JOIN booklet AS b
      ON b.region_key = r.region_key
     AND b.file_date = q.file_date
     AND b.indicator_code = i.indicator_code
    WHERE i.source_code = 'ba_wz_heft'
      AND i.source_column IS NOT NULL;

    RETURN QUERY
    SELECT i.source_code, d.level, d.value_status, COUNT(*)
    FROM normalized.de_indicator_value AS d
    JOIN normalized.de_indicator AS i USING (indicator_code)
    WHERE i.source_code IN ('ba_regionalreport', 'ba_wz_heft')
      AND d.geography_version = p_geography_version
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3;
END;
$$;

-- ---------------------------------------------------------------------------
-- Table 2.3 in wide form per Kreis and quarter (newest file), by position.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.de_ba_regionalreport_wide(p_file_date date)
RETURNS TABLE(
    kreis_key text,
    quarter_date date,
    v bigint[],
    s boolean[]
)
    LANGUAGE sql STABLE
    AS $$
    SELECT
        r.kreis_key,
        r.quarter_date,
        array_agg(CASE WHEN r.value ~ '^[0-9]+$' THEN r.value::bigint END ORDER BY r.sheet_row),
        array_agg(r.value = '*' ORDER BY r.sheet_row)
    FROM raw.de_ba_regionalreport AS r
    WHERE r.file_date = p_file_date AND r.table_name = '2.3'
    GROUP BY r.kreis_key, r.quarter_date
$$;

-- ---------------------------------------------------------------------------
-- Check: identities within table 2.3 of every Kreis and quarter. Positions:
-- 1 total, 2 A, 3 B,D,E, 4 C, 5-7 the three groups of C, 8 F, 9 G, 10 H,
-- 11 I, 12 J, 13 K, 14 L,M, 15 N, 16 N without temporary agency work,
-- 17 temporary agency work, 18 O,U, 19 P, 20 86, 21 87-88, 22 R,S,T,
-- 23 unassigned, 24 A (sectors), 25 B-F, 26 G-U. Exact where no part is
-- suppressed; otherwise the published total must be at least the visible sum.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_ba_sector_identities(p_file_date date)
RETURNS TABLE(
    rule text,
    kreis_key text,
    quarter_date date,
    published bigint,
    summed bigint,
    suppressed_parts integer
)
    LANGUAGE sql STABLE
    AS $$
    WITH w AS (SELECT * FROM normalized.de_ba_regionalreport_wide(p_file_date)),
    rules(rule, lhs, parts) AS (
        VALUES
            ('total = sections + unassigned', 1,
             ARRAY[2, 3, 4, 8, 9, 10, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 23]),
            ('C = three manufacturing groups', 4, ARRAY[5, 6, 7]),
            ('N = N without temporary agency work + temporary agency work', 15, ARRAY[16, 17]),
            ('B-F = B,D,E + C + F', 25, ARRAY[3, 4, 8]),
            ('G-U = sections G to U', 26, ARRAY[9, 10, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22]),
            ('A (sectors) = A', 24, ARRAY[2]),
            ('total = A + B-F + G-U + unassigned', 1, ARRAY[24, 25, 26, 23])
    ),
    evaluated AS (
        SELECT r.rule, w.kreis_key, w.quarter_date, w.v[r.lhs] AS published,
               (SELECT COALESCE(SUM(w.v[p]), 0) FROM unnest(r.parts) AS p)::bigint AS summed,
               (SELECT COUNT(*) FILTER (WHERE w.s[p]) FROM unnest(r.parts) AS p)::integer AS suppressed_parts,
               w.s[r.lhs] AS lhs_suppressed
        FROM w CROSS JOIN rules AS r
    )
    SELECT rule, kreis_key, quarter_date, published, summed, suppressed_parts
    FROM evaluated
    WHERE NOT lhs_suppressed
      AND ((suppressed_parts = 0 AND published <> summed)
        OR (suppressed_parts > 0 AND published < summed))
$$;

-- ---------------------------------------------------------------------------
-- Check: totals across files, levels and sources for one file date.
--   kreise_to_land        Σ Kreis totals (2.3) = Land total (2.1)
--   laender_to_germany    Σ Land totals (2.1) = Germany total (2.1)
--   land_report_vs_wz     Land total (2.1) = WZ booklet total, same quarter
--   kreise_group_vs_wz    Σ Kreis group (2.3) = Σ WZ divisions of the group.
--                         Where one side has suppressed parts, the complete
--                         side is "published" and must be at least the
--                         incomplete one; where both have, published is NULL
--                         (not comparable).
--   wz_divisions_to_total Σ WZ divisions + 'XX' = WZ total, per Land and Germany
--   wz_laender_to_germany Σ Länder vs Germany, WZ total and each division
--   laender_to_germany    Σ Länder vs Germany (2.1). Germany includes employees
--                         who cannot be assigned to a Land, so it may exceed
--                         the sum (rule applied in the Dagster check).
--   unassigned_vs_gemeindedaten Germany - Σ Länder (2.1) = the Gemeindedaten's
--                         row "keine Angabe/keine Zuordnung möglich" (employees
--                         at place of work), where both exist for the date
--   kreis_vs_gemeindedaten Kreis total (2.3) = BA Gemeindedaten employees at
--                         place of work, where both exist for the date
-- suppressed_parts > 0: the published value must be at least the sum.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_ba_sector_totals(
    p_file_date date,
    p_geography_version text
) RETURNS TABLE(
    comparison text,
    region_key text,
    item text,
    quarter_date date,
    published bigint,
    summed bigint,
    suppressed_parts bigint
)
    LANGUAGE sql STABLE
    AS $$
    WITH w AS MATERIALIZED (SELECT * FROM normalized.de_ba_regionalreport_wide(p_file_date)),
    land_rows AS MATERIALIZED (
        SELECT DISTINCT left(r.kreis_key, 2) AS land_key, r.quarter_date,
               r.value::bigint AS total
        FROM raw.de_ba_regionalreport AS r
        WHERE r.file_date = p_file_date AND r.table_name = '2.1'
          AND r.region_label LIKE 'Bundesland %' AND r.row_code = 'Insgesamt'
    ),
    germany_row AS MATERIALIZED (
        SELECT DISTINCT r.quarter_date, r.value::bigint AS total
        FROM raw.de_ba_regionalreport AS r
        WHERE r.file_date = p_file_date AND r.table_name = '2.1'
          AND r.region_label = 'Deutschland' AND r.row_code = 'Insgesamt'
    ),
    wz AS MATERIALIZED (
        SELECT b.region_key, b.wz_code, b.file_date,
               CASE WHEN b.column_values[1] ~ '^[0-9]+$' THEN b.column_values[1]::bigint END AS value,
               b.column_values[1] = '*' AS suppressed
        FROM raw.de_ba_wz_heft AS b
    ),
    groups(item, position, wz_prefix) AS (
        SELECT c.derived_code, g.position, substr(c.component_code, 4)
        FROM normalized.de_indicator_sum_component AS c
        JOIN (VALUES ('wz_sum_c', 4), ('wz_sum_consumer_goods', 5),
                     ('wz_sum_metal_electrical_steel', 6),
                     ('wz_sum_intermediate_goods', 7)) AS g(code, position)
          ON g.code = c.derived_code
    ),
    group_items AS (SELECT DISTINCT item, position FROM groups)
    SELECT 'kreise_to_land', l.land_key, 'total', l.quarter_date, l.total,
           SUM(w.v[1])::bigint, COUNT(*) FILTER (WHERE w.s[1])
    FROM land_rows AS l
    JOIN w ON left(w.kreis_key, 2) = l.land_key AND w.quarter_date = l.quarter_date
    GROUP BY l.land_key, l.quarter_date, l.total
    UNION ALL
    SELECT 'laender_to_germany', 'DG', 'total', g.quarter_date, g.total,
           SUM(l.total)::bigint, 0
    FROM germany_row AS g JOIN land_rows AS l USING (quarter_date)
    GROUP BY g.quarter_date, g.total
    UNION ALL
    SELECT 'land_report_vs_wz', l.land_key, 'total', l.quarter_date, z.value, l.total,
           CASE WHEN z.suppressed THEN 1 ELSE 0 END
    FROM land_rows AS l
    JOIN wz AS z ON z.region_key = l.land_key AND z.wz_code = 'total' AND z.file_date = l.quarter_date
    UNION ALL
    SELECT 'kreise_group_vs_wz', k.land_key, k.item, k.quarter_date,
           CASE WHEN k.suppressed = 0 THEN k.summed
                WHEN z.suppressed = 0 THEN z.value END,
           CASE WHEN k.suppressed = 0 THEN z.value ELSE k.summed END,
           k.suppressed + z.suppressed
    FROM (
        SELECT left(w.kreis_key, 2) AS land_key, g.item, w.quarter_date,
               SUM(w.v[g.position])::bigint AS summed,
               COUNT(*) FILTER (WHERE w.s[g.position]) AS suppressed
        FROM w CROSS JOIN group_items AS g
        GROUP BY 1, 2, 3
    ) AS k
    JOIN (
        SELECT z.region_key, g.item, z.file_date, SUM(z.value)::bigint AS value,
               COUNT(*) FILTER (WHERE z.suppressed) AS suppressed
        FROM wz AS z JOIN groups AS g ON g.wz_prefix = z.wz_code
        GROUP BY 1, 2, 3
    ) AS z ON z.region_key = k.land_key AND z.item = k.item AND z.file_date = k.quarter_date
    UNION ALL
    SELECT 'wz_divisions_to_total', t.region_key, 'total', t.file_date, t.value,
           SUM(d.value)::bigint, COUNT(*) FILTER (WHERE d.suppressed)
    FROM wz AS t
    JOIN wz AS d
      ON d.region_key = t.region_key AND d.file_date = t.file_date
     AND (d.wz_code ~ '^[0-9]{2}$' OR d.wz_code = 'XX')
    WHERE t.wz_code = 'total' AND NOT t.suppressed
    GROUP BY t.region_key, t.file_date, t.value
    UNION ALL
    SELECT 'wz_laender_to_germany', 'DG', g.wz_code, g.file_date, g.value,
           SUM(l.value)::bigint, COUNT(*) FILTER (WHERE l.suppressed)
    FROM wz AS g
    JOIN wz AS l
      ON l.wz_code = g.wz_code AND l.file_date = g.file_date AND l.region_key <> 'DG'
    WHERE g.region_key = 'DG' AND NOT g.suppressed
      AND (g.wz_code ~ '^[0-9]{2}$' OR g.wz_code IN ('total', '241'))
    GROUP BY g.wz_code, g.file_date, g.value
    UNION ALL
    SELECT 'unassigned_vs_gemeindedaten', 'DG', 'total', g.quarter_date, u.column_09::bigint,
           g.total - (SELECT SUM(l.total) FROM land_rows AS l WHERE l.quarter_date = g.quarter_date)::bigint,
           0
    FROM germany_row AS g
    JOIN raw.de_ba_gemeindedaten AS u
      ON u.reference_date = g.quarter_date
     AND u.sheet_name = 'Länder' AND u.source_level = 'unassigned'
     AND u.column_09 ~ '^[0-9]+$'
    UNION ALL
    SELECT 'kreis_vs_gemeindedaten', w.kreis_key, 'total', w.quarter_date, d.value::bigint,
           w.v[1], CASE WHEN w.s[1] THEN 1 ELSE 0 END
    FROM w
    JOIN normalized.de_indicator_value AS d
      ON d.level = 'krs' AND d.region_key = w.kreis_key
     AND d.indicator_code = 'employees_workplace'
     AND d.reference_date = w.quarter_date
     AND d.geography_version = p_geography_version
     AND d.value_status = 'value'
$$;

-- ---------------------------------------------------------------------------
-- Reviewed notes on the quality of a source's values (for example values a
-- source published but that are evidently incomplete). Filled from the
-- contracts in Python; shown with the region on the map.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_indicator_quality_note (
    source_code text NOT NULL REFERENCES normalized.de_indicator_source,
    level text NOT NULL CHECK (level IN ('sta', 'lan', 'krs', 'gem')),
    region_key text NOT NULL,
    reference_date date NOT NULL,
    note_de text NOT NULL,
    PRIMARY KEY (source_code, level, region_key, reference_date)
);

-- ---------------------------------------------------------------------------
-- Serving: newest quarter of each source, per Kreis (Regionalreport), Land
-- and Germany (WZ booklets). The four divisions exist only for Länder and
-- Germany; for Kreise their status is 'not_published_at_level'.
-- Groups at Land and Germany level are own calculations (<group>_basis):
--   sum_of_divisions  Σ the Land's published WZ divisions of the group, if
--                     every division is published;
--   sum_of_kreise     otherwise Σ the Land's Kreis values (Regionalreport), if
--                     every Kreis value is published and no quality note
--                     exists for the Kreise of that Land and quarter.
-- Both agree exactly wherever both are complete (check
-- de_ba_sector_source_totals). Otherwise the group is 'suppressed'. A share
-- is computed only from two values.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytical.de_current_sectors AS
WITH geo AS (
    SELECT MAX(d.geography_version) AS geography_version
    FROM normalized.de_indicator_value AS d
    JOIN normalized.de_indicator AS i USING (indicator_code)
    WHERE i.source_code IN ('ba_regionalreport', 'ba_wz_heft')
),
dates AS (
    SELECT
        MAX(d.reference_date) FILTER (WHERE d.indicator_code = 'sector_total') AS kreis_date,
        MAX(d.reference_date) FILTER (WHERE d.indicator_code = 'wz_total') AS land_date
    FROM normalized.de_indicator_value AS d, geo
    WHERE d.geography_version = geo.geography_version
      AND d.indicator_code IN ('sector_total', 'wz_total')
),
items(indicator_code, item) AS (
    VALUES
        ('sector_total', 'total'), ('sector_c', 'manufacturing'),
        ('sector_c_metal_electrical_steel', 'metal_electrical_steel'),
        ('sector_c_intermediate_goods', 'intermediate_goods'),
        ('sector_c_consumer_goods', 'consumer_goods'),
        ('wz_total', 'total'), ('wz_29', 'motor_vehicles'), ('wz_28', 'machinery'),
        ('wz_20', 'chemicals'), ('wz_241', 'steel'),
        ('wz_sum_c', 'manufacturing'),
        ('wz_sum_metal_electrical_steel', 'metal_electrical_steel'),
        ('wz_sum_intermediate_goods', 'intermediate_goods'),
        ('wz_sum_consumer_goods', 'consumer_goods')
),
values_ AS (
    SELECT d.*,
           CASE WHEN d.level = 'krs' THEN dates.kreis_date ELSE dates.land_date END AS current_date_
    FROM normalized.de_indicator_value AS d, geo, dates
    WHERE d.geography_version = geo.geography_version
),
direct AS (
    -- Published values: Kreis rows of the Regionalreport, Land and Germany
    -- rows of the WZ booklets, at the current date and a year earlier.
    SELECT v.level, v.region_key, v.reference_date, v.current_date_, it.item,
           v.value, v.value_status, 'published'::text AS basis
    FROM values_ AS v JOIN items AS it USING (indicator_code)
    WHERE v.reference_date IN (v.current_date_, (v.current_date_ - interval '1 year')::date)
),
division_sums AS (
    SELECT v.level, v.region_key, v.reference_date, v.current_date_, it.item,
           CASE WHEN bool_and(v.value_status = 'value') THEN SUM(v.value) END AS value,
           CASE WHEN bool_or(v.value_status = 'suppressed') THEN 'suppressed'
                WHEN bool_or(v.value_status = 'not_reported') THEN 'not_reported'
                ELSE 'value' END AS value_status
    FROM values_ AS v
    JOIN normalized.de_indicator_sum_component AS c ON c.component_code = v.indicator_code
    JOIN items AS it ON it.indicator_code = c.derived_code
    WHERE v.level IN ('lan', 'sta')
      AND v.reference_date IN (v.current_date_, (v.current_date_ - interval '1 year')::date)
    GROUP BY 1, 2, 3, 4, 5
),
kreis_sums AS (
    -- Σ Kreise per Land for the same groups, at the Land dates.
    SELECT 'lan'::text AS level, left(v.region_key, 2) AS region_key, v.reference_date, it.item,
           CASE WHEN bool_and(v.value_status = 'value') THEN SUM(v.value) END AS value,
           bool_and(v.value_status = 'value') AS complete
    FROM normalized.de_indicator_value AS v, geo, dates
    JOIN items AS it ON TRUE
    WHERE v.geography_version = geo.geography_version
      AND v.level = 'krs'
      AND v.indicator_code = it.indicator_code
      AND it.indicator_code LIKE 'sector_c%'
      AND v.reference_date IN (dates.land_date, (dates.land_date - interval '1 year')::date)
    GROUP BY 1, 2, 3, 4
),
chosen_sums AS (
    SELECT d.level, d.region_key, d.reference_date, d.current_date_, d.item,
           CASE WHEN d.value_status = 'value' THEN d.value
                WHEN k.complete AND n.note_de IS NULL THEN k.value END AS value,
           CASE WHEN d.value_status = 'value' THEN 'value'
                WHEN k.complete AND n.note_de IS NULL THEN 'value'
                ELSE d.value_status END AS value_status,
           CASE WHEN d.value_status = 'value' THEN 'sum_of_divisions'
                WHEN k.complete AND n.note_de IS NULL THEN 'sum_of_kreise'
                ELSE 'sum_of_divisions' END AS basis
    FROM division_sums AS d
    LEFT JOIN kreis_sums AS k
      ON k.level = d.level AND k.region_key = d.region_key
     AND k.reference_date = d.reference_date AND k.item = d.item
    LEFT JOIN normalized.de_indicator_quality_note AS n
      ON n.source_code = 'ba_regionalreport' AND n.level = 'lan'
     AND n.region_key = d.region_key AND n.reference_date = d.reference_date
),
long_values AS (
    SELECT * FROM direct UNION ALL SELECT * FROM chosen_sums
),
wide AS (
    SELECT
        l.level,
        l.region_key,
        MAX(l.current_date_) AS reference_date,
        MAX(l.value) FILTER (WHERE l.item = 'total' AND l.reference_date = l.current_date_) AS employees_total,
        MAX(l.value_status) FILTER (WHERE l.item = 'total' AND l.reference_date = l.current_date_) AS employees_total_status,
        MAX(l.value) FILTER (WHERE l.item = 'manufacturing' AND l.reference_date = l.current_date_) AS manufacturing,
        MAX(l.value_status) FILTER (WHERE l.item = 'manufacturing' AND l.reference_date = l.current_date_) AS manufacturing_status,
        MAX(l.basis) FILTER (WHERE l.item = 'manufacturing' AND l.reference_date = l.current_date_) AS manufacturing_basis,
        MAX(l.value) FILTER (WHERE l.item = 'metal_electrical_steel' AND l.reference_date = l.current_date_) AS metal_electrical_steel,
        MAX(l.value_status) FILTER (WHERE l.item = 'metal_electrical_steel' AND l.reference_date = l.current_date_) AS metal_electrical_steel_status,
        MAX(l.basis) FILTER (WHERE l.item = 'metal_electrical_steel' AND l.reference_date = l.current_date_) AS metal_electrical_steel_basis,
        MAX(l.value) FILTER (WHERE l.item = 'metal_electrical_steel' AND l.reference_date < l.current_date_) AS metal_electrical_steel_previous_year,
        MAX(l.value_status) FILTER (WHERE l.item = 'metal_electrical_steel' AND l.reference_date < l.current_date_) AS metal_electrical_steel_previous_year_status,
        MAX(l.value) FILTER (WHERE l.item = 'intermediate_goods' AND l.reference_date = l.current_date_) AS intermediate_goods,
        MAX(l.value_status) FILTER (WHERE l.item = 'intermediate_goods' AND l.reference_date = l.current_date_) AS intermediate_goods_status,
        MAX(l.basis) FILTER (WHERE l.item = 'intermediate_goods' AND l.reference_date = l.current_date_) AS intermediate_goods_basis,
        MAX(l.value) FILTER (WHERE l.item = 'consumer_goods' AND l.reference_date = l.current_date_) AS consumer_goods,
        MAX(l.value_status) FILTER (WHERE l.item = 'consumer_goods' AND l.reference_date = l.current_date_) AS consumer_goods_status,
        MAX(l.basis) FILTER (WHERE l.item = 'consumer_goods' AND l.reference_date = l.current_date_) AS consumer_goods_basis,
        MAX(l.value) FILTER (WHERE l.item = 'motor_vehicles' AND l.reference_date = l.current_date_) AS motor_vehicles,
        MAX(l.value_status) FILTER (WHERE l.item = 'motor_vehicles' AND l.reference_date = l.current_date_) AS motor_vehicles_status,
        MAX(l.value) FILTER (WHERE l.item = 'machinery' AND l.reference_date = l.current_date_) AS machinery,
        MAX(l.value_status) FILTER (WHERE l.item = 'machinery' AND l.reference_date = l.current_date_) AS machinery_status,
        MAX(l.value) FILTER (WHERE l.item = 'chemicals' AND l.reference_date = l.current_date_) AS chemicals,
        MAX(l.value_status) FILTER (WHERE l.item = 'chemicals' AND l.reference_date = l.current_date_) AS chemicals_status,
        MAX(l.value) FILTER (WHERE l.item = 'steel' AND l.reference_date = l.current_date_) AS steel,
        MAX(l.value_status) FILTER (WHERE l.item = 'steel' AND l.reference_date = l.current_date_) AS steel_status
    FROM long_values AS l
    GROUP BY l.level, l.region_key
)
SELECT
    w.level,
    w.region_key,
    COALESCE(k.kreis_name, la.land_name, 'Deutschland') AS region_name,
    COALESCE(k.land_key, CASE WHEN w.level = 'lan' THEN w.region_key END) AS land_key,
    w.reference_date,
    CASE WHEN w.level = 'krs' THEN 'ba_regionalreport' ELSE 'ba_wz_heft' END AS source_code,
    w.employees_total, COALESCE(w.employees_total_status, 'not_reported') AS employees_total_status,
    w.manufacturing, COALESCE(w.manufacturing_status, 'not_reported') AS manufacturing_status,
    w.manufacturing_basis,
    w.metal_electrical_steel, COALESCE(w.metal_electrical_steel_status, 'not_reported') AS metal_electrical_steel_status,
    w.metal_electrical_steel_basis,
    CASE WHEN w.metal_electrical_steel_status = 'value' AND w.employees_total_status = 'value'
          AND w.employees_total > 0
         THEN w.metal_electrical_steel / w.employees_total END AS metal_electrical_steel_share,
    CASE WHEN 'suppressed' IN (w.metal_electrical_steel_status, w.employees_total_status) THEN 'suppressed'
         WHEN w.metal_electrical_steel_status = 'value' AND w.employees_total_status = 'value'
              AND w.employees_total > 0 THEN 'value'
         WHEN w.employees_total = 0 THEN 'not_computable'
         ELSE 'not_reported' END AS metal_electrical_steel_share_status,
    w.metal_electrical_steel_previous_year,
    COALESCE(w.metal_electrical_steel_previous_year_status, 'not_reported') AS metal_electrical_steel_previous_year_status,
    w.intermediate_goods, COALESCE(w.intermediate_goods_status, 'not_reported') AS intermediate_goods_status,
    w.intermediate_goods_basis,
    w.consumer_goods, COALESCE(w.consumer_goods_status, 'not_reported') AS consumer_goods_status,
    w.consumer_goods_basis,
    w.motor_vehicles,
    CASE WHEN w.level = 'krs' THEN 'not_published_at_level' ELSE COALESCE(w.motor_vehicles_status, 'not_reported') END AS motor_vehicles_status,
    w.machinery,
    CASE WHEN w.level = 'krs' THEN 'not_published_at_level' ELSE COALESCE(w.machinery_status, 'not_reported') END AS machinery_status,
    w.chemicals,
    CASE WHEN w.level = 'krs' THEN 'not_published_at_level' ELSE COALESCE(w.chemicals_status, 'not_reported') END AS chemicals_status,
    w.steel,
    CASE WHEN w.level = 'krs' THEN 'not_published_at_level' ELSE COALESCE(w.steel_status, 'not_reported') END AS steel_status,
    n.note_de AS quality_note
FROM wide AS w
LEFT JOIN normalized.de_kreis AS k
  ON w.level = 'krs' AND k.kreis_key = w.region_key
 AND k.geography_version = (SELECT geography_version FROM geo)
LEFT JOIN normalized.de_land AS la
  ON w.level = 'lan' AND la.land_key = w.region_key
 AND la.geography_version = (SELECT geography_version FROM geo)
LEFT JOIN normalized.de_indicator_quality_note AS n
  ON n.level = w.level AND n.region_key = w.region_key AND n.reference_date = w.reference_date
 AND n.source_code = CASE WHEN w.level = 'krs' THEN 'ba_regionalreport' ELSE 'ba_wz_heft' END;

COMMENT ON VIEW analytical.de_current_sectors IS
    'Employees by sector, newest quarter: Kreise from the BA Regionalreport '
    '(sections and manufacturing groups as published), Länder and Germany '
    'from the BA WZ booklets (divisions 29, 28, 20, 24.1 as published; groups '
    'as sums of divisions or of Kreise, own calculation, see <group>_basis). '
    'Every value with a status; reviewed quality notes. Germany layer.';
