CREATE OR REPLACE FUNCTION normalized.check_icon_d2_ruc_weather_quality(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT
)
RETURNS TABLE (
    passed BOOLEAN,
    normalized_row_count BIGINT,
    expected_mask_cell_count BIGINT,
    bridge_cell_count BIGINT,
    missing_mask_cell_count BIGINT,
    outside_mask_cell_count BIGINT,
    bridge_missing_value_count BIGINT,
    conversion_mismatch_count BIGINT,
    rejected_partition_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
WITH scope AS (
    SELECT
        MIN(source_row.source_grid_id) AS source_grid_id,
        MIN(source_row.geography_version) AS geography_version,
        MIN(source_row.mask_buffer_m) AS mask_buffer_m,
        MIN(source_row.retained_point_count)::BIGINT
            AS mask_cell_count
    FROM raw.icon_d2_ruc_source AS source_row
    WHERE source_row.run_time_utc = p_run_time_utc
      AND source_row.lead_time = p_lead_time
),
weather_rows AS (
    SELECT weather_row.*
    FROM normalized.icon_d2_ruc_weather AS weather_row
    CROSS JOIN scope
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time
      AND weather_row.source_grid_id = scope.source_grid_id
      AND weather_row.geography_version = scope.geography_version
),
mask_cells AS (
    SELECT mask_row.cell_index
    FROM normalized.icon_weather_mask AS mask_row
    CROSS JOIN scope
    WHERE mask_row.source_grid_id = scope.source_grid_id
      AND mask_row.geography_version = scope.geography_version
      AND mask_row.mask_buffer_m = scope.mask_buffer_m
),
bridge_cells AS (
    SELECT DISTINCT bridge_row.cell_index
    FROM normalized.icon_plr_area_bridge AS bridge_row
    CROSS JOIN scope
    WHERE bridge_row.source_grid_id = scope.source_grid_id
      AND bridge_row.geography_version = scope.geography_version
),
counts AS (
    SELECT
        (SELECT COUNT(*) FROM weather_rows)::BIGINT
            AS normalized_row_count,
        (SELECT COUNT(*) FROM bridge_cells)::BIGINT
            AS bridge_cell_count,
        (
            SELECT COUNT(*)
            FROM mask_cells AS mask_cell
            WHERE NOT EXISTS (
                SELECT 1
                FROM weather_rows AS weather_row
                WHERE weather_row.cell_index = mask_cell.cell_index
            )
        )::BIGINT AS missing_mask_cell_count,
        (
            SELECT COUNT(*)
            FROM weather_rows AS weather_row
            WHERE NOT EXISTS (
                SELECT 1
                FROM mask_cells AS mask_cell
                WHERE mask_cell.cell_index = weather_row.cell_index
            )
        )::BIGINT AS outside_mask_cell_count,
        (
            SELECT COUNT(*)
            FROM bridge_cells AS bridge_cell
            LEFT JOIN weather_rows AS weather_row
              ON weather_row.cell_index = bridge_cell.cell_index
            WHERE weather_row.cell_index IS NULL
               OR weather_row.temperature_c IS NULL
               OR weather_row.relative_humidity_percent IS NULL
               OR weather_row.dew_point_temperature_c IS NULL
               OR weather_row.wind_u_10m_ms IS NULL
               OR weather_row.wind_v_10m_ms IS NULL
        )::BIGINT AS bridge_missing_value_count,
        (
            SELECT COUNT(*)
            FROM normalized.weather_partition_rejected AS rejected_row
            WHERE rejected_row.run_time_utc = p_run_time_utc
              AND rejected_row.lead_time = p_lead_time
        )::BIGINT AS rejected_partition_count
),
conversion_mismatches AS (
    SELECT
        COUNT(*)::BIGINT AS conversion_mismatch_count
    FROM weather_rows AS weather_row
    LEFT JOIN raw.icon_d2_ruc_field AS temperature_row
      ON temperature_row.run_time_utc = p_run_time_utc
     AND temperature_row.lead_time = p_lead_time
     AND temperature_row.indicator = 'T_2M'
     AND temperature_row.cell_index = weather_row.cell_index
    LEFT JOIN raw.icon_d2_ruc_field AS humidity_row
      ON humidity_row.run_time_utc = p_run_time_utc
     AND humidity_row.lead_time = p_lead_time
     AND humidity_row.indicator = 'RELHUM_2M'
     AND humidity_row.cell_index = weather_row.cell_index
    LEFT JOIN raw.icon_d2_ruc_field AS dew_point_row
      ON dew_point_row.run_time_utc = p_run_time_utc
     AND dew_point_row.lead_time = p_lead_time
     AND dew_point_row.indicator = 'TD_2M'
     AND dew_point_row.cell_index = weather_row.cell_index
    LEFT JOIN raw.icon_d2_ruc_field AS wind_u_row
      ON wind_u_row.run_time_utc = p_run_time_utc
     AND wind_u_row.lead_time = p_lead_time
     AND wind_u_row.indicator = 'U_10M'
     AND wind_u_row.cell_index = weather_row.cell_index
    LEFT JOIN raw.icon_d2_ruc_field AS wind_v_row
      ON wind_v_row.run_time_utc = p_run_time_utc
     AND wind_v_row.lead_time = p_lead_time
     AND wind_v_row.indicator = 'V_10M'
     AND wind_v_row.cell_index = weather_row.cell_index
    WHERE weather_row.temperature_c
            IS DISTINCT FROM temperature_row.source_value - 273.15
       OR weather_row.relative_humidity_percent
            IS DISTINCT FROM humidity_row.source_value
       OR weather_row.dew_point_temperature_c
            IS DISTINCT FROM dew_point_row.source_value - 273.15
       OR weather_row.wind_u_10m_ms
            IS DISTINCT FROM wind_u_row.source_value
       OR weather_row.wind_v_10m_ms
            IS DISTINCT FROM wind_v_row.source_value
)
SELECT
    (
        counts.normalized_row_count = scope.mask_cell_count
        AND counts.missing_mask_cell_count = 0
        AND counts.outside_mask_cell_count = 0
        AND counts.bridge_cell_count > 0
        AND counts.bridge_missing_value_count = 0
        AND conversion_mismatches.conversion_mismatch_count = 0
        AND counts.rejected_partition_count = 0
    ) AS passed,
    counts.normalized_row_count,
    scope.mask_cell_count AS expected_mask_cell_count,
    counts.bridge_cell_count,
    counts.missing_mask_cell_count,
    counts.outside_mask_cell_count,
    counts.bridge_missing_value_count,
    conversion_mismatches.conversion_mismatch_count,
    counts.rejected_partition_count
FROM scope
CROSS JOIN counts
CROSS JOIN conversion_mismatches;
$$;
