CREATE OR REPLACE FUNCTION normalized.refresh_icon_d2_ruc_weather(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT
)
RETURNS TABLE (
    accepted BOOLEAN,
    normalized_row_count BIGINT,
    expected_mask_cell_count BIGINT,
    bridge_cell_count BIGINT,
    bridge_missing_value_count BIGINT,
    invalid_unit_indicator_count BIGINT,
    rejection_reason TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_manifest_count BIGINT;
    v_valid_time_utc TIMESTAMPTZ;
    v_source_grid_id TEXT;
    v_geography_version TEXT;
    v_mask_buffer_m INTEGER;
    v_mask_cell_count BIGINT;
    v_scope_variant_count BIGINT;
    v_invalid_unit_count BIGINT;
    v_normalized_row_count BIGINT;
    v_bridge_cell_count BIGINT;
    v_bridge_missing_count BIGINT;
    v_raw_quality RECORD;
    v_reason TEXT;
    v_details JSONB;
BEGIN
    DELETE FROM normalized.icon_d2_ruc_weather AS weather_row
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time;

    DELETE FROM normalized.weather_partition_rejected AS rejected_row
    WHERE rejected_row.run_time_utc = p_run_time_utc
      AND rejected_row.lead_time = p_lead_time;

    SELECT
        COUNT(*)::BIGINT,
        MIN(source_row.valid_time_utc),
        MIN(source_row.source_grid_id),
        MIN(source_row.geography_version),
        MIN(source_row.mask_buffer_m),
        MIN(source_row.retained_point_count)::BIGINT,
        (
            COUNT(DISTINCT source_row.valid_time_utc)
            + COUNT(DISTINCT source_row.source_grid_id)
            + COUNT(DISTINCT source_row.geography_version)
            + COUNT(DISTINCT source_row.mask_buffer_m)
            + COUNT(DISTINCT source_row.retained_point_count)
        )::BIGINT
    INTO
        v_manifest_count,
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        v_mask_buffer_m,
        v_mask_cell_count,
        v_scope_variant_count
    FROM raw.icon_d2_ruc_source AS source_row
    WHERE source_row.run_time_utc = p_run_time_utc
      AND source_row.lead_time = p_lead_time;

    IF v_manifest_count <> 5
       OR v_scope_variant_count <> 5 THEN
        v_reason := 'raw_manifest_scope_incomplete_or_inconsistent';
        v_details := jsonb_build_object(
            'manifest_count', v_manifest_count,
            'scope_variant_sum', v_scope_variant_count
        );

        INSERT INTO normalized.weather_partition_rejected (
            run_time_utc,
            lead_time,
            rejection_reason,
            observed_indicators,
            observed_row_counts,
            source_grid_id,
            geography_version,
            mask_buffer_m,
            rejection_details
        )
        SELECT
            p_run_time_utc,
            p_lead_time,
            v_reason,
            COALESCE(
                jsonb_agg(
                    source_row.indicator
                    ORDER BY source_row.indicator
                ),
                '[]'::JSONB
            ),
            '{}'::JSONB,
            v_source_grid_id,
            v_geography_version,
            v_mask_buffer_m,
            v_details
        FROM raw.icon_d2_ruc_source AS source_row
        WHERE source_row.run_time_utc = p_run_time_utc
          AND source_row.lead_time = p_lead_time;

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            COALESCE(v_mask_cell_count, 0),
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            v_reason;
        RETURN;
    END IF;

    SELECT *
    INTO v_raw_quality
    FROM raw.check_icon_d2_ruc_field_partition(
        p_run_time_utc,
        p_lead_time,
        v_valid_time_utc
    );

    IF v_raw_quality IS NULL
       OR NOT v_raw_quality.passed THEN
        v_reason := 'raw_partition_quality_failed';
        v_details := to_jsonb(v_raw_quality);

        INSERT INTO normalized.weather_partition_rejected (
            run_time_utc,
            lead_time,
            rejection_reason,
            observed_indicators,
            observed_row_counts,
            source_grid_id,
            geography_version,
            mask_buffer_m,
            rejection_details
        )
        VALUES (
            p_run_time_utc,
            p_lead_time,
            v_reason,
            NULL,
            CASE
                WHEN v_raw_quality IS NULL
                    THEN NULL
                ELSE v_raw_quality.per_indicator_row_counts
            END,
            v_source_grid_id,
            v_geography_version,
            v_mask_buffer_m,
            v_details
        );

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            v_mask_cell_count,
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            v_reason;
        RETURN;
    END IF;

    SELECT
        COUNT(*)::BIGINT
    INTO v_invalid_unit_count
    FROM raw.icon_d2_ruc_source AS source_row
    WHERE source_row.run_time_utc = p_run_time_utc
      AND source_row.lead_time = p_lead_time
      AND NOT (
        (
            source_row.indicator IN ('T_2M', 'TD_2M')
            AND lower(
                replace(
                    replace(source_row.source_unit, ' ', ''),
                    '·',
                    ''
                )
            ) IN ('k', 'kelvin')
        )
        OR (
            source_row.indicator = 'RELHUM_2M'
            AND lower(
                replace(
                    replace(source_row.source_unit, ' ', ''),
                    '·',
                    ''
                )
            ) IN ('%', 'percent')
        )
        OR (
            source_row.indicator IN ('U_10M', 'V_10M')
            AND lower(
                replace(
                    replace(source_row.source_unit, ' ', ''),
                    '·',
                    ''
                )
            ) IN (
                'm/s',
                'ms-1',
                'ms**-1',
                'ms^-1'
            )
        )
    );

    IF v_invalid_unit_count > 0 THEN
        v_reason := 'unexpected_source_unit';

        INSERT INTO normalized.weather_partition_rejected (
            run_time_utc,
            lead_time,
            rejection_reason,
            observed_indicators,
            observed_row_counts,
            source_grid_id,
            geography_version,
            mask_buffer_m,
            rejection_details
        )
        SELECT
            p_run_time_utc,
            p_lead_time,
            v_reason,
            jsonb_agg(
                jsonb_build_object(
                    'indicator',
                    source_row.indicator,
                    'source_unit',
                    source_row.source_unit
                )
                ORDER BY source_row.indicator
            ),
            v_raw_quality.per_indicator_row_counts,
            v_source_grid_id,
            v_geography_version,
            v_mask_buffer_m,
            jsonb_build_object(
                'invalid_unit_indicator_count',
                v_invalid_unit_count
            )
        FROM raw.icon_d2_ruc_source AS source_row
        WHERE source_row.run_time_utc = p_run_time_utc
          AND source_row.lead_time = p_lead_time;

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            v_mask_cell_count,
            0::BIGINT,
            0::BIGINT,
            v_invalid_unit_count,
            v_reason;
        RETURN;
    END IF;

    INSERT INTO normalized.icon_d2_ruc_weather (
        run_time_utc,
        lead_time,
        valid_time_utc,
        source_grid_id,
        geography_version,
        mask_buffer_m,
        cell_index,
        temperature_c,
        relative_humidity_percent,
        dew_point_temperature_c,
        wind_u_10m_ms,
        wind_v_10m_ms
    )
    WITH pivoted AS (
        SELECT
            field_row.cell_index,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'T_2M'
            ) AS temperature_k,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'RELHUM_2M'
            ) AS relative_humidity_percent,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'TD_2M'
            ) AS dew_point_temperature_k,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'U_10M'
            ) AS wind_u_10m_ms,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'V_10M'
            ) AS wind_v_10m_ms
        FROM raw.icon_d2_ruc_field AS field_row
        WHERE field_row.run_time_utc = p_run_time_utc
          AND field_row.lead_time = p_lead_time
        GROUP BY field_row.cell_index
    )
    SELECT
        p_run_time_utc,
        p_lead_time,
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        v_mask_buffer_m,
        pivoted.cell_index,
        pivoted.temperature_k - 273.15,
        pivoted.relative_humidity_percent,
        pivoted.dew_point_temperature_k - 273.15,
        pivoted.wind_u_10m_ms,
        pivoted.wind_v_10m_ms
    FROM pivoted;

    GET DIAGNOSTICS v_normalized_row_count = ROW_COUNT;

    SELECT
        COUNT(DISTINCT bridge_row.cell_index)::BIGINT
    INTO v_bridge_cell_count
    FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.source_grid_id = v_source_grid_id
      AND bridge_row.geography_version = v_geography_version;

    SELECT
        COUNT(*)::BIGINT
    INTO v_bridge_missing_count
    FROM (
        SELECT DISTINCT
            bridge_row.cell_index
        FROM normalized.icon_plr_area_bridge AS bridge_row
        WHERE bridge_row.source_grid_id = v_source_grid_id
          AND bridge_row.geography_version = v_geography_version
    ) AS bridge_cell
    LEFT JOIN normalized.icon_d2_ruc_weather AS weather_row
      ON weather_row.run_time_utc = p_run_time_utc
     AND weather_row.lead_time = p_lead_time
     AND weather_row.source_grid_id = v_source_grid_id
     AND weather_row.geography_version = v_geography_version
     AND weather_row.cell_index = bridge_cell.cell_index
    WHERE weather_row.cell_index IS NULL
       OR weather_row.temperature_c IS NULL
       OR weather_row.relative_humidity_percent IS NULL
       OR weather_row.dew_point_temperature_c IS NULL
       OR weather_row.wind_u_10m_ms IS NULL
       OR weather_row.wind_v_10m_ms IS NULL;

    IF v_normalized_row_count <> v_mask_cell_count
       OR v_bridge_missing_count > 0 THEN
        v_reason := CASE
            WHEN v_normalized_row_count <> v_mask_cell_count
                THEN 'normalized_mask_row_count_mismatch'
            ELSE 'bridge_weather_values_incomplete'
        END;

        DELETE FROM normalized.icon_d2_ruc_weather AS weather_row
        WHERE weather_row.run_time_utc = p_run_time_utc
          AND weather_row.lead_time = p_lead_time;

        INSERT INTO normalized.weather_partition_rejected (
            run_time_utc,
            lead_time,
            rejection_reason,
            observed_indicators,
            observed_row_counts,
            source_grid_id,
            geography_version,
            mask_buffer_m,
            rejection_details
        )
        VALUES (
            p_run_time_utc,
            p_lead_time,
            v_reason,
            NULL,
            v_raw_quality.per_indicator_row_counts,
            v_source_grid_id,
            v_geography_version,
            v_mask_buffer_m,
            jsonb_build_object(
                'normalized_row_count',
                v_normalized_row_count,
                'expected_mask_cell_count',
                v_mask_cell_count,
                'bridge_cell_count',
                v_bridge_cell_count,
                'bridge_missing_value_count',
                v_bridge_missing_count
            )
        );

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            v_mask_cell_count,
            v_bridge_cell_count,
            v_bridge_missing_count,
            v_invalid_unit_count,
            v_reason;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        TRUE,
        v_normalized_row_count,
        v_mask_cell_count,
        v_bridge_cell_count,
        v_bridge_missing_count,
        v_invalid_unit_count,
        NULL::TEXT;
END;
$$;
