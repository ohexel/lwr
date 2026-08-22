-- Canonical Phase 6 bridge quality contract.
--
-- Remove every overload of the old function name. It is no longer
-- referenced by application code; the canonical function is the
-- explicitly named *_quality function below.

DO $$
DECLARE
    stale_function RECORD;
BEGIN
    FOR stale_function IN
        SELECT
            function_row.oid::regprocedure::TEXT AS signature
        FROM pg_proc AS function_row
        JOIN pg_namespace AS namespace_row
          ON namespace_row.oid = function_row.pronamespace
        WHERE namespace_row.nspname = 'normalized'
          AND function_row.proname = 'check_icon_plr_area_bridge'
    LOOP
        EXECUTE format(
            'DROP FUNCTION %s',
            stale_function.signature
        );
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION normalized.check_icon_plr_area_bridge_quality(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_expected_plr_count INTEGER DEFAULT 542,
    p_weight_tolerance DOUBLE PRECISION DEFAULT 0.000001
)
RETURNS TABLE (
    passed BOOLEAN,
    bridge_row_count BIGINT,
    source_plr_count BIGINT,
    represented_plr_count BIGINT,
    represented_icon_cell_count BIGINT,
    missing_plr_count BIGINT,
    orphan_plr_count BIGINT,
    orphan_icon_cell_count BIGINT,
    nonpositive_area_count BIGINT,
    invalid_fraction_count BIGINT,
    plr_weight_failure_count BIGINT,
    max_plr_weight_error DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
WITH source_plrs AS (
    SELECT
        plr_row.plr_id,
        plr_row.geography_version
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version
),
bridge_rows AS (
    SELECT bridge_row.*
    FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id
),
bridge_summary AS (
    SELECT
        COUNT(*)::BIGINT AS bridge_row_count,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT
            AS represented_plr_count,
        COUNT(DISTINCT bridge_row.cell_index)::BIGINT
            AS represented_icon_cell_count
    FROM bridge_rows AS bridge_row
),
source_summary AS (
    SELECT COUNT(*)::BIGINT AS source_plr_count
    FROM source_plrs
),
missing_plrs AS (
    SELECT COUNT(*)::BIGINT AS missing_plr_count
    FROM source_plrs AS plr_row
    WHERE NOT EXISTS (
        SELECT 1
        FROM bridge_rows AS bridge_row
        WHERE bridge_row.plr_id = plr_row.plr_id
          AND bridge_row.geography_version = plr_row.geography_version
    )
),
orphan_plrs AS (
    SELECT COUNT(*)::BIGINT AS orphan_plr_count
    FROM bridge_rows AS bridge_row
    LEFT JOIN normalized.plr AS plr_row
      ON plr_row.plr_id = bridge_row.plr_id
     AND plr_row.geography_version = bridge_row.geography_version
    WHERE plr_row.plr_id IS NULL
),
orphan_icon_cells AS (
    SELECT COUNT(*)::BIGINT AS orphan_icon_cell_count
    FROM bridge_rows AS bridge_row
    LEFT JOIN normalized.icon_cell AS icon_row
      ON icon_row.source_grid_id = bridge_row.source_grid_id
     AND icon_row.cell_index = bridge_row.cell_index
    WHERE icon_row.cell_index IS NULL
),
bad_areas AS (
    SELECT COUNT(*)::BIGINT AS nonpositive_area_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.intersection_area_m2 <= 0
       OR bridge_row.plr_area_m2 <= 0
       OR bridge_row.icon_cell_area_m2 <= 0
),
bad_fractions AS (
    SELECT COUNT(*)::BIGINT AS invalid_fraction_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.fraction_of_plr <= 0
       OR bridge_row.fraction_of_plr > 1 + p_weight_tolerance
       OR bridge_row.fraction_of_icon_cell <= 0
       OR bridge_row.fraction_of_icon_cell > 1 + p_weight_tolerance
),
plr_weight_sums AS (
    SELECT
        bridge_row.plr_id,
        SUM(bridge_row.fraction_of_plr)::DOUBLE PRECISION
            AS fraction_sum
    FROM bridge_rows AS bridge_row
    GROUP BY bridge_row.plr_id
),
weight_summary AS (
    SELECT
        COUNT(*) FILTER (
            WHERE ABS(weight_row.fraction_sum - 1.0)
                > p_weight_tolerance
        )::BIGINT AS plr_weight_failure_count,
        COALESCE(
            MAX(ABS(weight_row.fraction_sum - 1.0)),
            0.0
        )::DOUBLE PRECISION AS max_plr_weight_error
    FROM plr_weight_sums AS weight_row
)
SELECT
    (
        source_summary.source_plr_count = p_expected_plr_count
        AND bridge_summary.bridge_row_count > 0
        AND bridge_summary.represented_plr_count
            = source_summary.source_plr_count
        AND missing_plrs.missing_plr_count = 0
        AND orphan_plrs.orphan_plr_count = 0
        AND orphan_icon_cells.orphan_icon_cell_count = 0
        AND bad_areas.nonpositive_area_count = 0
        AND bad_fractions.invalid_fraction_count = 0
        AND weight_summary.plr_weight_failure_count = 0
    ) AS passed,
    bridge_summary.bridge_row_count,
    source_summary.source_plr_count,
    bridge_summary.represented_plr_count,
    bridge_summary.represented_icon_cell_count,
    missing_plrs.missing_plr_count,
    orphan_plrs.orphan_plr_count,
    orphan_icon_cells.orphan_icon_cell_count,
    bad_areas.nonpositive_area_count,
    bad_fractions.invalid_fraction_count,
    weight_summary.plr_weight_failure_count,
    weight_summary.max_plr_weight_error
FROM bridge_summary
CROSS JOIN source_summary
CROSS JOIN missing_plrs
CROSS JOIN orphan_plrs
CROSS JOIN orphan_icon_cells
CROSS JOIN bad_areas
CROSS JOIN bad_fractions
CROSS JOIN weight_summary;
$$;
