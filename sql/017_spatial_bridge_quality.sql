CREATE OR REPLACE FUNCTION normalized.check_icon_plr_area_bridge(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_expected_plr_count INTEGER DEFAULT 542,
    p_weight_tolerance DOUBLE PRECISION DEFAULT 0.00001
)
RETURNS TABLE (
    passed BOOLEAN,
    bridge_row_count BIGINT,
    represented_plr_count BIGINT,
    uncovered_plr_count BIGINT,
    intersecting_icon_cell_count BIGINT,
    non_positive_intersection_count BIGINT,
    invalid_fraction_count BIGINT,
    weight_sum_failure_count BIGINT,
    max_fraction_of_plr_deviation DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
WITH relevant_plrs AS (
    SELECT plr.plr_id
    FROM normalized.plr AS plr
    WHERE plr.geography_version = p_geography_version
),
bridge_summary AS (
    SELECT
        COUNT(*) AS bridge_row_count,
        COUNT(DISTINCT bridge.plr_id) AS represented_plr_count,
        COUNT(DISTINCT bridge.cell_index) AS intersecting_icon_cell_count,
        COUNT(*) FILTER (
            WHERE bridge.intersection_area_m2 <= 0
        ) AS non_positive_intersection_count,
        COUNT(*) FILTER (
            WHERE bridge.fraction_of_plr <= 0
               OR bridge.fraction_of_plr > 1 + p_weight_tolerance
               OR bridge.fraction_of_icon_cell <= 0
               OR bridge.fraction_of_icon_cell > 1 + p_weight_tolerance
        ) AS invalid_fraction_count
    FROM normalized.icon_plr_area_bridge AS bridge
    WHERE bridge.geography_version = p_geography_version
      AND bridge.source_grid_id = p_source_grid_id
),
coverage_summary AS (
    SELECT COUNT(*) AS uncovered_plr_count
    FROM relevant_plrs AS plr
    WHERE NOT EXISTS (
        SELECT 1
        FROM normalized.icon_plr_area_bridge AS bridge
        WHERE bridge.geography_version = p_geography_version
          AND bridge.source_grid_id = p_source_grid_id
          AND bridge.plr_id = plr.plr_id
    )
),
weight_by_plr AS (
    SELECT
        bridge.plr_id,
        SUM(bridge.fraction_of_plr) AS weight_sum
    FROM normalized.icon_plr_area_bridge AS bridge
    WHERE bridge.geography_version = p_geography_version
      AND bridge.source_grid_id = p_source_grid_id
    GROUP BY bridge.plr_id
),
weight_summary AS (
    SELECT
        COUNT(*) FILTER (
            WHERE ABS(weight_by_plr.weight_sum - 1.0)
                > p_weight_tolerance
        ) AS weight_sum_failure_count,
        COALESCE(
            MAX(ABS(weight_by_plr.weight_sum - 1.0)),
            0.0
        ) AS max_fraction_of_plr_deviation
    FROM weight_by_plr
)
SELECT
    (
        bridge_summary.bridge_row_count > 0
        AND bridge_summary.represented_plr_count = p_expected_plr_count
        AND coverage_summary.uncovered_plr_count = 0
        AND bridge_summary.non_positive_intersection_count = 0
        AND bridge_summary.invalid_fraction_count = 0
        AND weight_summary.weight_sum_failure_count = 0
    ) AS passed,
    bridge_summary.bridge_row_count,
    bridge_summary.represented_plr_count,
    coverage_summary.uncovered_plr_count,
    bridge_summary.intersecting_icon_cell_count,
    bridge_summary.non_positive_intersection_count,
    bridge_summary.invalid_fraction_count,
    weight_summary.weight_sum_failure_count,
    weight_summary.max_fraction_of_plr_deviation
FROM bridge_summary
CROSS JOIN coverage_summary
CROSS JOIN weight_summary
$$;
