CREATE OR REPLACE FUNCTION raw.check_icon_d2_ruc_field_partition(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT,
    p_expected_valid_time_utc TIMESTAMPTZ,
    p_expected_cell_count INTEGER DEFAULT 542040
)
RETURNS TABLE (
    passed BOOLEAN,
    total_row_count BIGINT,
    expected_row_count BIGINT,
    indicator_count BIGINT,
    missing_indicator_count BIGINT,
    unexpected_indicator_count BIGINT,
    wrong_row_count_indicator_count BIGINT,
    wrong_valid_time_row_count BIGINT,
    invalid_cell_index_row_count BIGINT,
    inconsistent_source_metadata_indicator_count BIGINT,
    null_source_value_count BIGINT,
    per_indicator_row_counts JSONB
)
LANGUAGE sql
STABLE
AS $$
WITH expected_indicators(indicator) AS (
    VALUES
        ('T_2M'::TEXT),
        ('RELHUM_2M'::TEXT),
        ('TD_2M'::TEXT),
        ('U_10M'::TEXT),
        ('V_10M'::TEXT)
),
partition_rows AS (
    SELECT
        weather_row.run_time_utc,
        weather_row.lead_time,
        weather_row.valid_time_utc,
        weather_row.indicator,
        weather_row.cell_index,
        weather_row.source_value,
        weather_row.source_unit,
        weather_row.source_url,
        weather_row.raw_path,
        weather_row.source_sha256
    FROM raw.icon_d2_ruc_field AS weather_row
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time
),
observed_indicators AS (
    SELECT
        weather_row.indicator,
        COUNT(*)::BIGINT AS row_count,
        (
            MIN(weather_row.source_unit)
                = MAX(weather_row.source_unit)
            AND MIN(weather_row.source_url)
                = MAX(weather_row.source_url)
            AND MIN(weather_row.raw_path)
                = MAX(weather_row.raw_path)
            AND MIN(weather_row.source_sha256)
                = MAX(weather_row.source_sha256)
        ) AS source_metadata_consistent
    FROM partition_rows AS weather_row
    GROUP BY weather_row.indicator
),
summary AS (
    SELECT
        COUNT(*)::BIGINT AS total_row_count,
        COUNT(
            DISTINCT weather_row.indicator
        )::BIGINT AS indicator_count,
        COUNT(*) FILTER (
            WHERE weather_row.valid_time_utc
                <> p_expected_valid_time_utc
        )::BIGINT AS wrong_valid_time_row_count,
        COUNT(*) FILTER (
            WHERE weather_row.cell_index < 0
               OR weather_row.cell_index
                    >= p_expected_cell_count
        )::BIGINT AS invalid_cell_index_row_count,
        COUNT(*) FILTER (
            WHERE weather_row.source_value IS NULL
        )::BIGINT AS null_source_value_count
    FROM partition_rows AS weather_row
),
missing_indicators AS (
    SELECT
        COUNT(*)::BIGINT AS missing_indicator_count
    FROM expected_indicators AS expected
    WHERE NOT EXISTS (
        SELECT 1
        FROM observed_indicators AS observed
        WHERE observed.indicator = expected.indicator
    )
),
unexpected_indicators AS (
    SELECT
        COUNT(*)::BIGINT AS unexpected_indicator_count
    FROM observed_indicators AS observed
    WHERE NOT EXISTS (
        SELECT 1
        FROM expected_indicators AS expected
        WHERE expected.indicator = observed.indicator
    )
),
wrong_row_counts AS (
    SELECT
        COUNT(*)::BIGINT
            AS wrong_row_count_indicator_count
    FROM expected_indicators AS expected
    LEFT JOIN observed_indicators AS observed
      ON observed.indicator = expected.indicator
    WHERE COALESCE(
        observed.row_count,
        0
    ) <> p_expected_cell_count
),
source_metadata_consistency AS (
    SELECT
        COUNT(*)::BIGINT
            AS inconsistent_source_metadata_indicator_count
    FROM observed_indicators AS observed
    WHERE NOT observed.source_metadata_consistent
),
indicator_counts AS (
    SELECT
        COALESCE(
            jsonb_object_agg(
                expected.indicator,
                COALESCE(
                    observed.row_count,
                    0
                )
                ORDER BY expected.indicator
            ),
            '{}'::JSONB
        ) AS per_indicator_row_counts
    FROM expected_indicators AS expected
    LEFT JOIN observed_indicators AS observed
      ON observed.indicator = expected.indicator
)
SELECT
    (
        summary.total_row_count
            = p_expected_cell_count * 5::BIGINT
        AND summary.indicator_count = 5
        AND missing_indicators.missing_indicator_count = 0
        AND unexpected_indicators.unexpected_indicator_count = 0
        AND wrong_row_counts.wrong_row_count_indicator_count = 0
        AND summary.wrong_valid_time_row_count = 0
        AND summary.invalid_cell_index_row_count = 0
        AND source_metadata_consistency.inconsistent_source_metadata_indicator_count = 0
    ) AS passed,
    summary.total_row_count,
    (
        p_expected_cell_count * 5::BIGINT
    ) AS expected_row_count,
    summary.indicator_count,
    missing_indicators.missing_indicator_count,
    unexpected_indicators.unexpected_indicator_count,
    wrong_row_counts.wrong_row_count_indicator_count,
    summary.wrong_valid_time_row_count,
    summary.invalid_cell_index_row_count,
    source_metadata_consistency.inconsistent_source_metadata_indicator_count,
    summary.null_source_value_count,
    indicator_counts.per_indicator_row_counts
FROM summary
CROSS JOIN missing_indicators
CROSS JOIN unexpected_indicators
CROSS JOIN wrong_row_counts
CROSS JOIN source_metadata_consistency
CROSS JOIN indicator_counts;
$$;
