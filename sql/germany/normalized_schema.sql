-- Germany layer, normalized: administrative hierarchy and population.
-- Additive and idempotent: only de_-prefixed objects, no Berlin object is
-- touched. Geometry is EPSG:3035 (ETRS89-LAEA, equal-area), see D4.

-- ---------------------------------------------------------------------------
-- Hierarchy
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_land (
    geography_version text NOT NULL,
    land_key text NOT NULL CHECK (land_key ~ '^[0-9]{2}$'),
    land_name text NOT NULL,
    geometry public.geometry(MultiPolygon, 3035) NOT NULL,
    area_m2 double precision NOT NULL CHECK (area_m2 > 0),
    source_sha256 text NOT NULL,
    PRIMARY KEY (geography_version, land_key)
);

CREATE TABLE IF NOT EXISTS normalized.de_kreis (
    geography_version text NOT NULL,
    kreis_key text NOT NULL CHECK (kreis_key ~ '^[0-9]{5}$'),
    land_key text NOT NULL,
    kreis_name text NOT NULL,
    kreis_type text NOT NULL,
    nuts text,
    geometry public.geometry(MultiPolygon, 3035) NOT NULL,
    area_m2 double precision NOT NULL CHECK (area_m2 > 0),
    source_sha256 text NOT NULL,
    PRIMARY KEY (geography_version, kreis_key),
    FOREIGN KEY (geography_version, land_key)
        REFERENCES normalized.de_land (geography_version, land_key),
    CHECK (left(kreis_key, 2) = land_key)
);

CREATE TABLE IF NOT EXISTS normalized.de_gemeinde (
    geography_version text NOT NULL,
    ars text NOT NULL CHECK (ars ~ '^[0-9]{12}$'),
    ags text NOT NULL CHECK (ags ~ '^[0-9]{8}$'),
    kreis_key text NOT NULL,
    land_key text NOT NULL,
    gemeinde_name text NOT NULL,
    gemeinde_type text NOT NULL,
    is_unincorporated boolean NOT NULL,
    geometry public.geometry(MultiPolygon, 3035) NOT NULL,
    area_m2 double precision NOT NULL CHECK (area_m2 > 0),
    source_sha256 text NOT NULL,
    PRIMARY KEY (geography_version, ars),
    UNIQUE (geography_version, ags),
    FOREIGN KEY (geography_version, kreis_key)
        REFERENCES normalized.de_kreis (geography_version, kreis_key),
    FOREIGN KEY (geography_version, land_key)
        REFERENCES normalized.de_land (geography_version, land_key),
    CHECK (left(ars, 5) = kreis_key),
    CHECK (left(ars, 2) = land_key)
);

CREATE INDEX IF NOT EXISTS de_gemeinde_kreis_idx
    ON normalized.de_gemeinde (geography_version, kreis_key);
CREATE INDEX IF NOT EXISTS de_gemeinde_geometry_idx
    ON normalized.de_gemeinde USING gist (geometry);

-- ---------------------------------------------------------------------------
-- Population (Zensus 2022 total population, reference date 2022-05-15)
-- NULL never means 0: population_status says why a value is missing.
-- 'census_zero_or_set_to_zero' is defensive: no Gemeinde total in the pinned
-- extract is '-' (the Zensus sign for 'exactly zero or set to zero'), but
-- future extracts or tables may contain it. Kept so that such a value is
-- stored as 0 with this flag, not as NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS normalized.de_gemeinde_population (
    geography_version text NOT NULL,
    ars text NOT NULL,
    population_total integer CHECK (population_total >= 0),
    population_status text NOT NULL CHECK (
        population_status IN (
            'census_value',
            'census_zero_or_set_to_zero',
            'no_census_record_unincorporated',
            'no_census_record',
            'rejected_source_value'
        )
    ),
    source_value text,
    reference_date date NOT NULL,
    source_table text NOT NULL,
    source_sha256 text NOT NULL,
    PRIMARY KEY (geography_version, ars),
    FOREIGN KEY (geography_version, ars)
        REFERENCES normalized.de_gemeinde (geography_version, ars),
    CHECK (
        (population_status IN ('census_value', 'census_zero_or_set_to_zero'))
        = (population_total IS NOT NULL)
    )
);

-- Published totals at every level, kept for the source-total checks.
CREATE TABLE IF NOT EXISTS normalized.de_zensus_published_population (
    level text NOT NULL CHECK (level IN ('sta', 'lan', 'krs', 'gem')),
    region_key text NOT NULL,
    region_name text NOT NULL,
    population_total bigint CHECK (population_total >= 0),
    source_value text NOT NULL,
    reference_date date NOT NULL,
    source_table text NOT NULL,
    source_sha256 text NOT NULL,
    PRIMARY KEY (source_sha256, level, region_key)
);

-- ---------------------------------------------------------------------------
-- Refresh: hierarchy from VG250 land parts (GF = 4)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.de_to_3035_multipolygon(
    p_geometry public.geometry
) RETURNS public.geometry
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT public.ST_Multi(
        public.ST_CollectionExtract(
            public.ST_MakeValid(public.ST_Transform(p_geometry, 3035)),
            3
        )
    )::public.geometry(MultiPolygon, 3035)
$$;

CREATE OR REPLACE FUNCTION normalized.refresh_de_hierarchy(
    p_source_sha256 text,
    p_geography_version text
) RETURNS TABLE(
    land_count bigint,
    kreis_count bigint,
    gemeinde_count bigint,
    unincorporated_count bigint
)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM raw.de_vg250 WHERE source_sha256 = p_source_sha256
    ) THEN
        RAISE EXCEPTION 'No raw VG250 rows for source_sha256=%', p_source_sha256;
    END IF;

    -- A geography version is built once. Forecasts and the bridge reference its
    -- Gemeinden, so it is never deleted and rebuilt in place: the same VG250 file
    -- gives the same hierarchy (no-op); another file needs a new geography_version.
    IF EXISTS (
        SELECT 1 FROM normalized.de_gemeinde
        WHERE geography_version = p_geography_version
    ) THEN
        IF EXISTS (
            SELECT 1 FROM normalized.de_gemeinde
            WHERE geography_version = p_geography_version
              AND source_sha256 <> p_source_sha256
        ) THEN
            RAISE EXCEPTION
                'geography_version % was built from another VG250 file; '
                'load a new VG250 vintage under a new geography_version',
                p_geography_version;
        END IF;
        RAISE NOTICE 'geography_version % already built from this file; unchanged',
            p_geography_version;
        RETURN QUERY
        SELECT
            (SELECT COUNT(*) FROM normalized.de_land
             WHERE geography_version = p_geography_version),
            (SELECT COUNT(*) FROM normalized.de_kreis
             WHERE geography_version = p_geography_version),
            (SELECT COUNT(*) FROM normalized.de_gemeinde
             WHERE geography_version = p_geography_version),
            (SELECT COUNT(*) FROM normalized.de_gemeinde
             WHERE geography_version = p_geography_version
               AND is_unincorporated);
        RETURN;
    END IF;

    DELETE FROM normalized.de_gemeinde_population
    WHERE geography_version = p_geography_version;
    DELETE FROM normalized.de_gemeinde
    WHERE geography_version = p_geography_version;
    DELETE FROM normalized.de_kreis
    WHERE geography_version = p_geography_version;
    DELETE FROM normalized.de_land
    WHERE geography_version = p_geography_version;

    INSERT INTO normalized.de_land
    SELECT
        p_geography_version,
        raw_row.ars,
        raw_row.gen,
        geometry_3035,
        public.ST_Area(geometry_3035),
        p_source_sha256
    FROM raw.de_vg250 AS raw_row
    CROSS JOIN LATERAL (
        SELECT normalized.de_to_3035_multipolygon(raw_row.geometry_source)
            AS geometry_3035
    ) AS transformed
    WHERE raw_row.source_sha256 = p_source_sha256
      AND raw_row.level = 'lan'
      AND raw_row.gf = 4;

    INSERT INTO normalized.de_kreis
    SELECT
        p_geography_version,
        raw_row.ars,
        left(raw_row.ars, 2),
        raw_row.gen,
        raw_row.bez,
        raw_row.nuts,
        geometry_3035,
        public.ST_Area(geometry_3035),
        p_source_sha256
    FROM raw.de_vg250 AS raw_row
    CROSS JOIN LATERAL (
        SELECT normalized.de_to_3035_multipolygon(raw_row.geometry_source)
            AS geometry_3035
    ) AS transformed
    WHERE raw_row.source_sha256 = p_source_sha256
      AND raw_row.level = 'krs'
      AND raw_row.gf = 4;

    INSERT INTO normalized.de_gemeinde
    SELECT
        p_geography_version,
        raw_row.ars,
        raw_row.ags,
        left(raw_row.ars, 5),
        left(raw_row.ars, 2),
        raw_row.gen,
        raw_row.bez,
        raw_row.bez = 'Gemeindefreies Gebiet',
        geometry_3035,
        public.ST_Area(geometry_3035),
        p_source_sha256
    FROM raw.de_vg250 AS raw_row
    CROSS JOIN LATERAL (
        SELECT normalized.de_to_3035_multipolygon(raw_row.geometry_source)
            AS geometry_3035
    ) AS transformed
    WHERE raw_row.source_sha256 = p_source_sha256
      AND raw_row.level = 'gem'
      AND raw_row.gf = 4;

    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM normalized.de_land
         WHERE geography_version = p_geography_version),
        (SELECT COUNT(*) FROM normalized.de_kreis
         WHERE geography_version = p_geography_version),
        (SELECT COUNT(*) FROM normalized.de_gemeinde
         WHERE geography_version = p_geography_version),
        (SELECT COUNT(*) FROM normalized.de_gemeinde
         WHERE geography_version = p_geography_version
           AND is_unincorporated);
END;
$$;

-- ---------------------------------------------------------------------------
-- Refresh: population from the Zensus total rows (no age class)
-- '-' means "Genau Null oder auf Null geändert" (see docs/data-sources.md).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.de_zensus_total_rows(
    p_source_sha256s text[]
) RETURNS TABLE(
    level text,
    region_key text,
    region_name text,
    source_value text,
    population_total bigint,
    table_code text,
    source_sha256 text
)
    LANGUAGE sql STABLE
    AS $$
    SELECT DISTINCT ON (1, 2)
        CASE raw_row.variable_1_code
            WHEN 'GEODL1' THEN 'sta'
            WHEN 'GEOBL1' THEN 'lan'
            WHEN 'GEOLK4' THEN 'krs'
            WHEN 'GEOGM4' THEN 'gem'
        END,
        raw_row.variable_1_attribute_code,
        raw_row.variable_1_attribute_label,
        raw_row.value,
        CASE
            WHEN raw_row.value ~ '^[0-9]+$' THEN raw_row.value::bigint
            WHEN raw_row.value = '-' THEN 0
        END,
        raw_row.table_code,
        raw_row.source_sha256
    FROM raw.de_zensus2022_population AS raw_row
    WHERE raw_row.source_sha256 = ANY(p_source_sha256s)
      AND raw_row.variable_2_attribute_code IS NULL
      AND raw_row.value_unit = 'Anzahl'
      AND raw_row.variable_1_code IN ('GEODL1', 'GEOBL1', 'GEOLK4', 'GEOGM4')
    ORDER BY 1, 2, raw_row.source_sha256
$$;

CREATE OR REPLACE FUNCTION normalized.refresh_de_population(
    p_geography_version text,
    p_source_sha256s text[],
    p_reference_date date
) RETURNS TABLE(
    gemeinde_count bigint,
    census_value_count bigint,
    zero_count bigint,
    unincorporated_without_record bigint,
    missing_record_count bigint,
    rejected_count bigint,
    published_row_count bigint
)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM normalized.de_gemeinde
        WHERE geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION 'No Gemeinden for geography_version=%', p_geography_version;
    END IF;

    -- Every file repeats the Germany row; all copies must agree.
    IF EXISTS (
        SELECT 1
        FROM raw.de_zensus2022_population AS raw_row
        WHERE raw_row.source_sha256 = ANY(p_source_sha256s)
          AND raw_row.variable_2_attribute_code IS NULL
          AND raw_row.value_unit = 'Anzahl'
        GROUP BY raw_row.variable_1_code, raw_row.variable_1_attribute_code
        HAVING COUNT(DISTINCT raw_row.value) > 1
    ) THEN
        RAISE EXCEPTION 'Zensus extracts disagree on a published total';
    END IF;

    DELETE FROM normalized.de_zensus_published_population
    WHERE source_sha256 = ANY(p_source_sha256s);

    INSERT INTO normalized.de_zensus_published_population
    SELECT level, region_key, region_name, population_total, source_value,
           p_reference_date, table_code, source_sha256
    FROM normalized.de_zensus_total_rows(p_source_sha256s);

    DELETE FROM normalized.de_gemeinde_population
    WHERE geography_version = p_geography_version;

    INSERT INTO normalized.de_gemeinde_population
    SELECT
        gemeinde.geography_version,
        gemeinde.ars,
        census.population_total,
        CASE
            WHEN census.region_key IS NULL AND gemeinde.is_unincorporated
                THEN 'no_census_record_unincorporated'
            WHEN census.region_key IS NULL THEN 'no_census_record'
            WHEN census.source_value = '-' THEN 'census_zero_or_set_to_zero'
            WHEN census.population_total IS NULL THEN 'rejected_source_value'
            ELSE 'census_value'
        END,
        census.source_value,
        p_reference_date,
        COALESCE(census.table_code, '1000A-1003'),
        COALESCE(census.source_sha256, 'none')
    FROM normalized.de_gemeinde AS gemeinde
    LEFT JOIN normalized.de_zensus_total_rows(p_source_sha256s) AS census
      ON census.level = 'gem'
     AND census.region_key = gemeinde.ars
    WHERE gemeinde.geography_version = p_geography_version;

    RETURN QUERY
    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE population_status = 'census_value'),
        COUNT(*) FILTER (WHERE population_status = 'census_zero_or_set_to_zero'),
        COUNT(*) FILTER (WHERE population_status = 'no_census_record_unincorporated'),
        COUNT(*) FILTER (WHERE population_status = 'no_census_record'),
        COUNT(*) FILTER (WHERE population_status = 'rejected_source_value'),
        (SELECT COUNT(*) FROM normalized.de_zensus_published_population
         WHERE source_sha256 = ANY(p_source_sha256s))
    FROM normalized.de_gemeinde_population
    WHERE geography_version = p_geography_version;
END;
$$;

-- ---------------------------------------------------------------------------
-- Check 1: key coverage. One row per exception; `blocking` decides pass/fail.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_key_coverage(
    p_geography_version text,
    p_source_sha256s text[]
) RETURNS TABLE(
    level text,
    issue text,
    region_key text,
    region_name text,
    blocking boolean
)
    LANGUAGE sql STABLE
    AS $$
    WITH published AS (
        SELECT * FROM normalized.de_zensus_published_population
        WHERE source_sha256 = ANY(p_source_sha256s)
    ),
    geometry_keys AS (
        SELECT 'lan' AS level, land_key AS region_key, land_name AS region_name,
               false AS is_unincorporated
        FROM normalized.de_land WHERE geography_version = p_geography_version
        UNION ALL
        SELECT 'krs', kreis_key, kreis_name, false
        FROM normalized.de_kreis WHERE geography_version = p_geography_version
        UNION ALL
        SELECT 'gem', ars, gemeinde_name, is_unincorporated
        FROM normalized.de_gemeinde WHERE geography_version = p_geography_version
    )
    SELECT g.level,
           CASE WHEN g.is_unincorporated
                THEN 'unincorporated_area_without_census_record'
                ELSE 'geometry_without_census_record' END,
           g.region_key, g.region_name,
           NOT g.is_unincorporated
    FROM geometry_keys AS g
    LEFT JOIN published AS p
      ON p.level = g.level AND p.region_key = g.region_key
    WHERE p.region_key IS NULL
    UNION ALL
    SELECT p.level, 'census_record_without_geometry', p.region_key,
           p.region_name, true
    FROM published AS p
    LEFT JOIN geometry_keys AS g
      ON g.level = p.level AND g.region_key = p.region_key
    WHERE p.level <> 'sta' AND g.region_key IS NULL
    UNION ALL
    SELECT 'gem', 'rejected_census_value', pop.ars, gem.gemeinde_name, true
    FROM normalized.de_gemeinde_population AS pop
    JOIN normalized.de_gemeinde AS gem USING (geography_version, ars)
    WHERE pop.geography_version = p_geography_version
      AND pop.population_status = 'rejected_source_value'
    ORDER BY 5 DESC, 1, 3
$$;

-- ---------------------------------------------------------------------------
-- Check 2: hierarchy integrity beyond what foreign keys guarantee. Foreign
-- keys already reject a Gemeinde or Kreis whose parent does not exist. This
-- check covers what keys cannot express: no empty parents, a Gemeinde's Land
-- equals its Kreis's Land, Gemeinde keys agree with VG250's key parts. One row
-- per violation; empty = pass. Area consistency is checked separately.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_hierarchy(
    p_geography_version text
) RETURNS TABLE(
    issue text,
    region_key text,
    detail text
)
    LANGUAGE sql STABLE
    AS $$
    SELECT 'kreis_without_gemeinde', k.kreis_key, k.kreis_name
    FROM normalized.de_kreis AS k
    WHERE k.geography_version = p_geography_version
      AND NOT EXISTS (
          SELECT 1 FROM normalized.de_gemeinde AS g
          WHERE g.geography_version = k.geography_version
            AND g.kreis_key = k.kreis_key
      )
    UNION ALL
    SELECT 'land_without_kreis', l.land_key, l.land_name
    FROM normalized.de_land AS l
    WHERE l.geography_version = p_geography_version
      AND NOT EXISTS (
          SELECT 1 FROM normalized.de_kreis AS k
          WHERE k.geography_version = l.geography_version
            AND k.land_key = l.land_key
      )
    UNION ALL
    SELECT 'gemeinde_land_differs_from_kreis_land', g.ars,
           g.land_key || ' vs ' || k.land_key
    FROM normalized.de_gemeinde AS g
    JOIN normalized.de_kreis AS k
      USING (geography_version, kreis_key)
    WHERE g.geography_version = p_geography_version
      AND g.land_key <> k.land_key
    UNION ALL
    SELECT 'gemeinde_key_parts_disagree', g.ars,
           concat_ws('|', r.sn_l, r.sn_r, r.sn_k)
    FROM normalized.de_gemeinde AS g
    JOIN raw.de_vg250 AS r
      ON r.source_sha256 = g.source_sha256
     AND r.level = 'gem' AND r.gf = 4 AND r.ars = g.ars
    WHERE g.geography_version = p_geography_version
      AND (r.sn_l || r.sn_r || r.sn_k) <> g.kreis_key
    ORDER BY 1, 2
$$;

-- Area consistency: Gemeinden should partition their Kreis. Reported, not
-- gating: VG250 is generalised, so tiny sliver differences are possible.
CREATE OR REPLACE FUNCTION normalized.de_kreis_area_partition(
    p_geography_version text
) RETURNS TABLE(
    kreis_key text,
    kreis_area_m2 double precision,
    gemeinde_area_sum_m2 double precision,
    relative_difference double precision
)
    LANGUAGE sql STABLE
    AS $$
    SELECT k.kreis_key, k.area_m2, SUM(g.area_m2),
           (SUM(g.area_m2) - k.area_m2) / k.area_m2
    FROM normalized.de_kreis AS k
    JOIN normalized.de_gemeinde AS g USING (geography_version, kreis_key)
    WHERE k.geography_version = p_geography_version
    GROUP BY k.kreis_key, k.area_m2
$$;

-- ---------------------------------------------------------------------------
-- Check 3: normalized Gemeinde population summed to Kreis, Land and Bund vs.
-- the totals Zensus 2022 publishes for those levels (same checksum-pinned
-- extracts). One row per published or derived region; FULL JOIN, so a
-- region present on only one side shows up with a NULL.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalized.check_de_source_totals(
    p_geography_version text,
    p_source_sha256s text[]
) RETURNS TABLE(
    level text,
    region_key text,
    region_name text,
    gemeinde_sum bigint,
    published_total bigint,
    difference bigint
)
    LANGUAGE sql STABLE
    AS $$
    WITH gem AS (
        SELECT g.kreis_key, g.land_key, p.population_total::bigint AS population
        FROM normalized.de_gemeinde_population AS p
        JOIN normalized.de_gemeinde AS g
          ON g.geography_version = p.geography_version AND g.ars = p.ars
        WHERE p.geography_version = p_geography_version
          AND p.population_total IS NOT NULL
    ),
    derived AS (
        SELECT 'krs'::text AS level, gem.kreis_key AS region_key, SUM(gem.population)::bigint AS s
        FROM gem GROUP BY gem.kreis_key
        UNION ALL
        SELECT 'lan', gem.land_key, SUM(gem.population)::bigint FROM gem GROUP BY gem.land_key
        UNION ALL
        SELECT 'sta', 'DG', SUM(gem.population)::bigint FROM gem  -- Zensus key of Germany
    ),
    published AS (
        SELECT z.level, z.region_key, z.region_name,
               z.population_total
        FROM normalized.de_zensus_published_population AS z
        WHERE z.source_sha256 = ANY (p_source_sha256s)
          AND z.level IN ('krs', 'lan', 'sta')
    )
    SELECT COALESCE(p.level, d.level),
           COALESCE(p.region_key, d.region_key),
           p.region_name,
           d.s,
           p.population_total,
           p.population_total - d.s
    FROM published AS p
    FULL JOIN derived AS d
      ON d.level = p.level AND d.region_key = p.region_key
    ORDER BY 1 DESC, 2
$$;
