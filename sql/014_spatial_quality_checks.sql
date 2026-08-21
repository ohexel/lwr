CREATE OR REPLACE FUNCTION normalized.check_plr_geometry_quality(
    p_source_sha256 TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    source_row_count BIGINT,
    normalized_row_count BIGINT,
    rejected_row_count BIGINT,
    invalid_normalized_geometry_count BIGINT,
    wrong_srid_count BIGINT,
    geography_version TEXT,
    rejection_reasons JSONB
)
LANGUAGE sql
STABLE
AS $$
WITH source_summary AS (
    SELECT
        COUNT(*) AS source_row_count,
        MIN(raw_lor.geography_version) AS geography_version
    FROM raw.lor_plr AS raw_lor
    WHERE raw_lor.source_sha256 = p_source_sha256
),
normalized_summary AS (
    SELECT
        COUNT(*) AS normalized_row_count,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(normalized_plr.geometry)
               OR ST_IsEmpty(normalized_plr.geometry)
               OR ST_Area(normalized_plr.geometry) <= 0
        ) AS invalid_normalized_geometry_count,
        COUNT(*) FILTER (
            WHERE ST_SRID(normalized_plr.geometry) <> 25833
        ) AS wrong_srid_count
    FROM normalized.plr AS normalized_plr
    WHERE normalized_plr.source_sha256 = p_source_sha256
),
rejected_summary AS (
    SELECT
        COUNT(*) AS rejected_row_count
    FROM normalized.plr_geometry_rejected AS rejected
    WHERE rejected.source_sha256 = p_source_sha256
),
reason_summary AS (
    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    ) AS rejection_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.plr_geometry_rejected AS rejected
        WHERE rejected.source_sha256 = p_source_sha256
        GROUP BY rejected.rejection_reason
    ) AS reason_counts
)
SELECT
    (
        source_summary.source_row_count = p_expected_plr_count
        AND normalized_summary.normalized_row_count
            = p_expected_plr_count
        AND rejected_summary.rejected_row_count = 0
        AND normalized_summary.invalid_normalized_geometry_count = 0
        AND normalized_summary.wrong_srid_count = 0
    ) AS passed,
    source_summary.source_row_count,
    normalized_summary.normalized_row_count,
    rejected_summary.rejected_row_count,
    normalized_summary.invalid_normalized_geometry_count,
    normalized_summary.wrong_srid_count,
    source_summary.geography_version,
    reason_summary.rejection_reasons
FROM source_summary
CROSS JOIN normalized_summary
CROSS JOIN rejected_summary
CROSS JOIN reason_summary
$$;


CREATE OR REPLACE FUNCTION normalized.check_icon_geometry_quality(
    p_source_grid_id TEXT,
    p_expected_vertex_count INTEGER DEFAULT 272089,
    p_expected_cell_count INTEGER DEFAULT 542040
)
RETURNS TABLE (
    passed BOOLEAN,
    raw_vertex_count BIGINT,
    raw_cell_count BIGINT,
    topology_row_count BIGINT,
    normalized_cell_count BIGINT,
    rejected_cell_count BIGINT,
    invalid_normalized_geometry_count BIGINT,
    wrong_srid_count BIGINT,
    non_triangle_count BIGINT,
    rejection_reasons JSONB
)
LANGUAGE sql
STABLE
AS $$
WITH raw_summary AS (
    SELECT
        (
            SELECT COUNT(*)
            FROM raw.icon_grid_vertex AS vertex
            WHERE vertex.source_grid_id = p_source_grid_id
        ) AS raw_vertex_count,
        (
            SELECT COUNT(DISTINCT topology.cell_index)
            FROM raw.icon_grid_cell_vertex AS topology
            WHERE topology.source_grid_id = p_source_grid_id
        ) AS raw_cell_count,
        (
            SELECT COUNT(*)
            FROM raw.icon_grid_cell_vertex AS topology
            WHERE topology.source_grid_id = p_source_grid_id
        ) AS topology_row_count
),
normalized_summary AS (
    SELECT
        COUNT(*) AS normalized_cell_count,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(cell.geometry)
               OR ST_IsEmpty(cell.geometry)
               OR ST_Area(cell.geometry) <= 0
        ) AS invalid_normalized_geometry_count,
        COUNT(*) FILTER (
            WHERE ST_SRID(cell.geometry) <> 25833
        ) AS wrong_srid_count,
        COUNT(*) FILTER (
            WHERE ST_NPoints(ST_ExteriorRing(cell.geometry)) <> 4
        ) AS non_triangle_count
    FROM normalized.icon_cell AS cell
    WHERE cell.source_grid_id = p_source_grid_id
),
rejected_summary AS (
    SELECT COUNT(*) AS rejected_cell_count
    FROM normalized.icon_geometry_rejected AS rejected
    WHERE rejected.source_grid_id = p_source_grid_id
),
reason_summary AS (
    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    ) AS rejection_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.icon_geometry_rejected AS rejected
        WHERE rejected.source_grid_id = p_source_grid_id
        GROUP BY rejected.rejection_reason
    ) AS reason_counts
)
SELECT
    (
        raw_summary.raw_vertex_count = p_expected_vertex_count
        AND raw_summary.raw_cell_count = p_expected_cell_count
        AND raw_summary.topology_row_count
            = p_expected_cell_count * 3
        AND normalized_summary.normalized_cell_count
            = p_expected_cell_count
        AND rejected_summary.rejected_cell_count = 0
        AND normalized_summary.invalid_normalized_geometry_count = 0
        AND normalized_summary.wrong_srid_count = 0
        AND normalized_summary.non_triangle_count = 0
    ) AS passed,
    raw_summary.raw_vertex_count,
    raw_summary.raw_cell_count,
    raw_summary.topology_row_count,
    normalized_summary.normalized_cell_count,
    rejected_summary.rejected_cell_count,
    normalized_summary.invalid_normalized_geometry_count,
    normalized_summary.wrong_srid_count,
    normalized_summary.non_triangle_count,
    reason_summary.rejection_reasons
FROM raw_summary
CROSS JOIN normalized_summary
CROSS JOIN rejected_summary
CROSS JOIN reason_summary
$$;
