CREATE OR REPLACE FUNCTION normalized.classify_afs_population(
    p_source_sha256 TEXT
)
RETURNS TABLE (
    plr_id TEXT,
    population_total BIGINT,
    population_65_79 BIGINT,
    population_80plus BIGINT,
    population_65plus BIGINT,
    share_65plus DOUBLE PRECISION,
    rejection_reason TEXT,
    reference_date DATE,
    publication_date DATE,
    source_sha256 TEXT
)
LANGUAGE sql
AS $$
WITH source_rows AS (
    SELECT
        NULLIF(BTRIM(plr_id_source), '') AS plr_id,
        population_total_source,
        population_65_79_source,
        population_80plus_source,
        reference_code_source,
        publication_date,
        source_sha256
    FROM raw.afs_population
    WHERE source_sha256 = p_source_sha256
),
parsed AS (
    SELECT
        plr_id,
        population_total_source,
        population_65_79_source,
        population_80plus_source,

        CASE
            WHEN NULLIF(BTRIM(population_total_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_total_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_total_source)::BIGINT
            ELSE NULL
        END AS population_total,

        CASE
            WHEN NULLIF(BTRIM(population_65_79_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_65_79_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_65_79_source)::BIGINT
            ELSE NULL
        END AS population_65_79,

        CASE
            WHEN NULLIF(BTRIM(population_80plus_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_80plus_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_80plus_source)::BIGINT
            ELSE NULL
        END AS population_80plus,

        NULLIF(BTRIM(population_total_source), '') IS NOT NULL
            AND BTRIM(population_total_source) !~ '^-?[0-9]+$'
            AS invalid_population_total_source,

        NULLIF(BTRIM(population_65_79_source), '') IS NOT NULL
            AND BTRIM(population_65_79_source) !~ '^-?[0-9]+$'
            AS invalid_population_65_79_source,

        NULLIF(BTRIM(population_80plus_source), '') IS NOT NULL
            AND BTRIM(population_80plus_source) !~ '^-?[0-9]+$'
            AS invalid_population_80plus_source,

        (
            TO_DATE(reference_code_source || '01', 'YYYYMMDD')
            + INTERVAL '1 month'
            - INTERVAL '1 day'
        )::DATE AS reference_date,

        publication_date,
        source_sha256
    FROM source_rows
),
derived AS (
    SELECT
        *,
        CASE
            WHEN population_65_79 IS NULL
              OR population_80plus IS NULL
                THEN NULL
            ELSE population_65_79 + population_80plus
        END AS population_65plus
    FROM parsed
),
classified AS (
    SELECT
        *,
        CASE
            WHEN population_total IS NULL
             AND NOT invalid_population_total_source
                THEN 'missing_population_total'

            WHEN invalid_population_total_source
                THEN 'invalid_population_total'

            WHEN (
                population_65_79 IS NULL
                AND NOT invalid_population_65_79_source
            )
              OR (
                population_80plus IS NULL
                AND NOT invalid_population_80plus_source
            )
                THEN 'missing_population_65plus_component'

            WHEN invalid_population_65_79_source
              OR invalid_population_80plus_source
                THEN 'invalid_population_65plus_component'

            WHEN population_total < 0
                THEN 'negative_population_total'

            WHEN population_total = 0
                THEN 'zero_population_total'

            WHEN population_65_79 < 0
              OR population_80plus < 0
              OR population_65plus < 0
              OR population_65_79 > population_total
              OR population_80plus > population_total
              OR population_65plus > population_total
                THEN 'invalid_population_65plus'

            ELSE NULL
        END AS rejection_reason
    FROM derived
)
SELECT
    plr_id,
    population_total,
    population_65_79,
    population_80plus,
    population_65plus,
    CASE
        WHEN rejection_reason IS NULL
            THEN population_65plus::DOUBLE PRECISION
                 / population_total::DOUBLE PRECISION
        ELSE NULL
    END AS share_65plus,
    rejection_reason,
    reference_date,
    publication_date,
    source_sha256
FROM classified
$$;


CREATE OR REPLACE FUNCTION normalized.refresh_plr_population(
    p_source_sha256 TEXT,
    p_expected_row_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    source_row_count BIGINT,
    accepted_row_count BIGINT,
    rejected_row_count BIGINT,
    rejection_reasons JSONB,
    reference_date DATE
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_count BIGINT;
    v_distinct_plr_count BIGINT;
    v_blank_plr_count BIGINT;
    v_reference_code_count BIGINT;
    v_reference_code TEXT;
    v_reference_date DATE;
    v_accepted_count BIGINT;
    v_rejected_count BIGINT;
    v_reasons JSONB;
BEGIN
    SELECT
        COUNT(*),
        COUNT(DISTINCT NULLIF(BTRIM(plr_id_source), '')),
        COUNT(*) FILTER (
            WHERE NULLIF(BTRIM(plr_id_source), '') IS NULL
        ),
        COUNT(DISTINCT reference_code_source),
        MIN(reference_code_source)
    INTO
        v_source_count,
        v_distinct_plr_count,
        v_blank_plr_count,
        v_reference_code_count,
        v_reference_code
    FROM raw.afs_population
    WHERE source_sha256 = p_source_sha256;

    IF v_source_count = 0 THEN
        RAISE EXCEPTION
            'No raw AfS population rows found for source_sha256 %',
            p_source_sha256;
    END IF;

    IF v_source_count <> p_expected_row_count THEN
        RAISE EXCEPTION
            'AfS population source row count mismatch: expected %, got %',
            p_expected_row_count,
            v_source_count;
    END IF;

    IF v_blank_plr_count <> 0 THEN
        RAISE EXCEPTION
            'AfS population source contains % blank or null PLR IDs',
            v_blank_plr_count;
    END IF;

    IF v_distinct_plr_count <> v_source_count THEN
        RAISE EXCEPTION
            'AfS population source contains duplicate PLR IDs';
    END IF;

    IF v_reference_code_count <> 1 THEN
        RAISE EXCEPTION
            'AfS population source must contain exactly one reference code; got %',
            v_reference_code_count;
    END IF;

    IF v_reference_code !~ '^[0-9]{6}$' THEN
        RAISE EXCEPTION
            'AfS population reference code must have YYYYMM format; got %',
            v_reference_code;
    END IF;

    v_reference_date := (
        TO_DATE(v_reference_code || '01', 'YYYYMMDD')
        + INTERVAL '1 month'
        - INTERVAL '1 day'
    )::DATE;

    DELETE FROM normalized.plr_population_65plus AS accepted
    WHERE accepted.reference_date = v_reference_date;

    DELETE FROM normalized.plr_population_rejected AS rejected
    WHERE rejected.reference_date = v_reference_date;

    INSERT INTO normalized.plr_population_65plus (
        plr_id,
        population_total,
        population_65_79,
        population_80plus,
        population_65plus,
        share_65plus,
        reference_date,
        publication_date,
        source_sha256
    )
    SELECT
        classified.plr_id,
        classified.population_total,
        classified.population_65_79,
        classified.population_80plus,
        classified.population_65plus,
        classified.share_65plus,
        classified.reference_date,
        classified.publication_date,
        classified.source_sha256
    FROM normalized.classify_afs_population(p_source_sha256) AS classified
    WHERE classified.rejection_reason IS NULL;

    GET DIAGNOSTICS v_accepted_count = ROW_COUNT;

    INSERT INTO normalized.plr_population_rejected (
        plr_id,
        population_total,
        population_65_79,
        population_80plus,
        population_65plus,
        share_65plus,
        rejection_reason,
        reference_date,
        publication_date,
        rejected_at_utc,
        source_sha256
    )
    SELECT
        classified.plr_id,
        classified.population_total,
        classified.population_65_79,
        classified.population_80plus,
        classified.population_65plus,
        classified.share_65plus,
        classified.rejection_reason,
        classified.reference_date,
        classified.publication_date,
        NOW(),
        classified.source_sha256
    FROM normalized.classify_afs_population(p_source_sha256) AS classified
    WHERE classified.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    IF v_accepted_count + v_rejected_count <> v_source_count THEN
        RAISE EXCEPTION
            'AfS population quality split failed accounting: '
            'source %, accepted %, rejected %',
            v_source_count,
            v_accepted_count,
            v_rejected_count;
    END IF;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(counts.rejection_reason, counts.reason_count),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.plr_population_rejected AS rejected
        WHERE rejected.source_sha256 = p_source_sha256
        GROUP BY rejection_reason
    ) AS counts;

    RETURN QUERY
    SELECT
        v_source_count,
        v_accepted_count,
        v_rejected_count,
        v_reasons,
        v_reference_date;
END
$$;
