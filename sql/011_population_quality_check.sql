CREATE OR REPLACE FUNCTION normalized.check_population_quality(
    p_source_sha256 TEXT
)
RETURNS TABLE (
    passed BOOLEAN,
    source_row_count BIGINT,
    accepted_row_count BIGINT,
    rejected_row_count BIGINT,
    accepted_rejected_overlap BIGINT,
    rejection_reasons JSONB
)
LANGUAGE sql
STABLE
AS $$
WITH source_counts AS (
    SELECT COUNT(*) AS source_row_count
    FROM raw.afs_population AS raw_population
    WHERE raw_population.source_sha256 = p_source_sha256
),
accepted_counts AS (
    SELECT COUNT(*) AS accepted_row_count
    FROM normalized.plr_population_65plus AS accepted
    WHERE accepted.source_sha256 = p_source_sha256
),
rejected_counts AS (
    SELECT COUNT(*) AS rejected_row_count
    FROM normalized.plr_population_rejected AS rejected
    WHERE rejected.source_sha256 = p_source_sha256
),
overlap_counts AS (
    SELECT COUNT(*) AS accepted_rejected_overlap
    FROM normalized.plr_population_65plus AS accepted
    JOIN normalized.plr_population_rejected AS rejected
      ON accepted.reference_date = rejected.reference_date
     AND accepted.plr_id = rejected.plr_id
    WHERE accepted.source_sha256 = p_source_sha256
      AND rejected.source_sha256 = p_source_sha256
),
reason_counts AS (
    SELECT
        rejected.rejection_reason,
        COUNT(*) AS reason_count
    FROM normalized.plr_population_rejected AS rejected
    WHERE rejected.source_sha256 = p_source_sha256
    GROUP BY rejected.rejection_reason
),
reason_summary AS (
    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    ) AS rejection_reasons
    FROM reason_counts
)
SELECT
    (
        source_counts.source_row_count
        = accepted_counts.accepted_row_count
          + rejected_counts.rejected_row_count
        AND overlap_counts.accepted_rejected_overlap = 0
        AND source_counts.source_row_count > 0
    ) AS passed,
    source_counts.source_row_count,
    accepted_counts.accepted_row_count,
    rejected_counts.rejected_row_count,
    overlap_counts.accepted_rejected_overlap,
    reason_summary.rejection_reasons
FROM source_counts
CROSS JOIN accepted_counts
CROSS JOIN rejected_counts
CROSS JOIN overlap_counts
CROSS JOIN reason_summary
$$;
