BEGIN;

CREATE OR REPLACE FUNCTION normalized.check_icon_weather_mask_quality(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_mask_buffer_m INTEGER DEFAULT 5000,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    source_plr_count BIGINT,
    mask_cell_count BIGINT,
    bridge_cell_count BIGINT,
    missing_bridge_cell_count BIGINT,
    orphan_mask_cell_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
WITH source_plrs AS (
    SELECT COUNT(*)::BIGINT AS source_plr_count
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version
),
mask_cells AS (
    SELECT mask_row.cell_index
    FROM normalized.icon_weather_mask AS mask_row
    WHERE mask_row.geography_version = p_geography_version
      AND mask_row.source_grid_id = p_source_grid_id
      AND mask_row.mask_buffer_m = p_mask_buffer_m
),
bridge_cells AS (
    SELECT DISTINCT bridge_row.cell_index
    FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id
),
counts AS (
    SELECT
        (SELECT COUNT(*) FROM mask_cells)::BIGINT AS mask_cell_count,
        (SELECT COUNT(*) FROM bridge_cells)::BIGINT AS bridge_cell_count,
        (
            SELECT COUNT(*)
            FROM bridge_cells AS bridge_cell
            WHERE NOT EXISTS (
                SELECT 1
                FROM mask_cells AS mask_cell
                WHERE mask_cell.cell_index = bridge_cell.cell_index
            )
        )::BIGINT AS missing_bridge_cell_count,
        (
            SELECT COUNT(*)
            FROM mask_cells AS mask_cell
            LEFT JOIN normalized.icon_cell AS icon_row
              ON icon_row.source_grid_id = p_source_grid_id
             AND icon_row.cell_index = mask_cell.cell_index
            WHERE icon_row.cell_index IS NULL
        )::BIGINT AS orphan_mask_cell_count
)
SELECT
    (
        source_plrs.source_plr_count = p_expected_plr_count
        AND counts.mask_cell_count > 0
        AND counts.bridge_cell_count > 0
        AND counts.mask_cell_count >= counts.bridge_cell_count
        AND counts.missing_bridge_cell_count = 0
        AND counts.orphan_mask_cell_count = 0
    ) AS passed,
    source_plrs.source_plr_count,
    counts.mask_cell_count,
    counts.bridge_cell_count,
    counts.missing_bridge_cell_count,
    counts.orphan_mask_cell_count
FROM source_plrs
CROSS JOIN counts;
$$;

CREATE OR REPLACE FUNCTION raw.check_icon_d2_ruc_field_partition(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT,
    p_expected_valid_time_utc TIMESTAMPTZ,
    p_expected_source_point_count INTEGER DEFAULT 542040
)
RETURNS TABLE (
    passed BOOLEAN,
    source_indicator_count BIGINT,
    field_indicator_count BIGINT,
    total_retained_row_count BIGINT,
    expected_retained_row_count BIGINT,
    mask_cell_count BIGINT,
    missing_indicator_count BIGINT,
    unexpected_indicator_count BIGINT,
    wrong_source_point_count_indicator_count BIGINT,
    wrong_retained_row_count_indicator_count BIGINT,
    wrong_valid_time_indicator_count BIGINT,
    inconsistent_scope_count BIGINT,
    outside_mask_row_count BIGINT,
    null_retained_value_count BIGINT,
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
source_rows AS (
    SELECT source_row.*
    FROM raw.icon_d2_ruc_source AS source_row
    WHERE source_row.run_time_utc = p_run_time_utc
      AND source_row.lead_time = p_lead_time
),
field_rows AS (
    SELECT field_row.*
    FROM raw.icon_d2_ruc_field AS field_row
    WHERE field_row.run_time_utc = p_run_time_utc
      AND field_row.lead_time = p_lead_time
),
scope AS (
    SELECT
        MIN(source_row.source_grid_id) AS source_grid_id,
        MIN(source_row.geography_version) AS geography_version,
        MIN(source_row.mask_buffer_m) AS mask_buffer_m,
        MIN(source_row.retained_point_count) AS mask_cell_count,
        (
            COUNT(DISTINCT source_row.source_grid_id)
            + COUNT(DISTINCT source_row.geography_version)
            + COUNT(DISTINCT source_row.mask_buffer_m)
            + COUNT(DISTINCT source_row.retained_point_count)
        )::BIGINT AS scope_variant_sum
    FROM source_rows AS source_row
),
observed_fields AS (
    SELECT
        field_row.indicator,
        COUNT(*)::BIGINT AS row_count
    FROM field_rows AS field_row
    GROUP BY field_row.indicator
),
missing_indicators AS (
    SELECT COUNT(*)::BIGINT AS missing_indicator_count
    FROM expected_indicators AS expected
    WHERE NOT EXISTS (
        SELECT 1
        FROM source_rows AS source_row
        WHERE source_row.indicator = expected.indicator
    )
),
unexpected_indicators AS (
    SELECT COUNT(*)::BIGINT AS unexpected_indicator_count
    FROM source_rows AS source_row
    WHERE NOT EXISTS (
        SELECT 1
        FROM expected_indicators AS expected
        WHERE expected.indicator = source_row.indicator
    )
),
wrong_source_counts AS (
    SELECT COUNT(*)::BIGINT AS wrong_source_point_count_indicator_count
    FROM source_rows AS source_row
    WHERE source_row.source_point_count <> p_expected_source_point_count
),
wrong_retained_counts AS (
    SELECT COUNT(*)::BIGINT AS wrong_retained_row_count_indicator_count
    FROM expected_indicators AS expected
    LEFT JOIN observed_fields AS observed
      ON observed.indicator = expected.indicator
    CROSS JOIN scope
    WHERE COALESCE(observed.row_count, 0)
        <> COALESCE(scope.mask_cell_count, 0)
),
wrong_valid_times AS (
    SELECT COUNT(*)::BIGINT AS wrong_valid_time_indicator_count
    FROM source_rows AS source_row
    WHERE source_row.valid_time_utc <> p_expected_valid_time_utc
),
outside_mask AS (
    SELECT COUNT(*)::BIGINT AS outside_mask_row_count
    FROM field_rows AS field_row
    CROSS JOIN scope
    LEFT JOIN normalized.icon_weather_mask AS mask_row
      ON mask_row.geography_version = scope.geography_version
     AND mask_row.source_grid_id = scope.source_grid_id
     AND mask_row.mask_buffer_m = scope.mask_buffer_m
     AND mask_row.cell_index = field_row.cell_index
    WHERE mask_row.cell_index IS NULL
),
summary AS (
    SELECT
        (SELECT COUNT(*) FROM source_rows)::BIGINT AS source_indicator_count,
        (SELECT COUNT(DISTINCT indicator) FROM field_rows)::BIGINT
            AS field_indicator_count,
        (SELECT COUNT(*) FROM field_rows)::BIGINT AS total_retained_row_count,
        (
            SELECT COUNT(*)
            FROM field_rows
            WHERE source_value IS NULL
        )::BIGINT AS null_retained_value_count
),
indicator_counts AS (
    SELECT COALESCE(
        jsonb_object_agg(
            expected.indicator,
            COALESCE(observed.row_count, 0)
            ORDER BY expected.indicator
        ),
        '{}'::JSONB
    ) AS per_indicator_row_counts
    FROM expected_indicators AS expected
    LEFT JOIN observed_fields AS observed
      ON observed.indicator = expected.indicator
)
SELECT
    (
        summary.source_indicator_count = 5
        AND summary.field_indicator_count = 5
        AND missing_indicators.missing_indicator_count = 0
        AND unexpected_indicators.unexpected_indicator_count = 0
        AND wrong_source_counts.wrong_source_point_count_indicator_count = 0
        AND wrong_retained_counts.wrong_retained_row_count_indicator_count = 0
        AND wrong_valid_times.wrong_valid_time_indicator_count = 0
        AND scope.scope_variant_sum = 4
        AND outside_mask.outside_mask_row_count = 0
        AND summary.total_retained_row_count
            = scope.mask_cell_count * 5::BIGINT
    ) AS passed,
    summary.source_indicator_count,
    summary.field_indicator_count,
    summary.total_retained_row_count,
    scope.mask_cell_count * 5::BIGINT AS expected_retained_row_count,
    scope.mask_cell_count,
    missing_indicators.missing_indicator_count,
    unexpected_indicators.unexpected_indicator_count,
    wrong_source_counts.wrong_source_point_count_indicator_count,
    wrong_retained_counts.wrong_retained_row_count_indicator_count,
    wrong_valid_times.wrong_valid_time_indicator_count,
    CASE WHEN scope.scope_variant_sum = 4 THEN 0 ELSE 1 END::BIGINT,
    outside_mask.outside_mask_row_count,
    summary.null_retained_value_count,
    indicator_counts.per_indicator_row_counts
FROM summary
CROSS JOIN scope
CROSS JOIN missing_indicators
CROSS JOIN unexpected_indicators
CROSS JOIN wrong_source_counts
CROSS JOIN wrong_retained_counts
CROSS JOIN wrong_valid_times
CROSS JOIN outside_mask
CROSS JOIN indicator_counts;
$$;

COMMIT;
