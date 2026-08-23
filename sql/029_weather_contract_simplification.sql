BEGIN;

-- Operational weather source contract:
-- T_2M, RELHUM_2M, U_10M, V_10M.
-- Raw PostgreSQL preserves replayable source values. Downstream
-- relations retain only values used by analytical products.

TRUNCATE TABLE
    analytical.plr_weather_population,
    analytical.plr_weather_population_rejected,
    analytical.plr_weather,
    normalized.icon_d2_ruc_weather,
    normalized.weather_partition_rejected;

-- TD_2M is no longer required. ON DELETE CASCADE removes its
-- Berlin-scoped raw field rows.
DELETE FROM raw.icon_d2_ruc_source
WHERE indicator = 'TD_2M';

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
        summary.source_indicator_count = 4
        AND summary.field_indicator_count = 4
        AND missing_indicators.missing_indicator_count = 0
        AND unexpected_indicators.unexpected_indicator_count = 0
        AND wrong_source_counts.wrong_source_point_count_indicator_count = 0
        AND wrong_retained_counts.wrong_retained_row_count_indicator_count = 0
        AND wrong_valid_times.wrong_valid_time_indicator_count = 0
        AND scope.scope_variant_sum = 4
        AND outside_mask.outside_mask_row_count = 0
        AND summary.total_retained_row_count
            = scope.mask_cell_count * 4::BIGINT
    ) AS passed,
    summary.source_indicator_count,
    summary.field_indicator_count,
    summary.total_retained_row_count,
    scope.mask_cell_count * 4::BIGINT AS expected_retained_row_count,
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

    IF v_manifest_count <> 4
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
            source_row.indicator = 'T_2M'
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
        temperature_c
    )
    SELECT
        p_run_time_utc,
        p_lead_time,
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        v_mask_buffer_m,
        field_row.cell_index,
        field_row.source_value - 273.15
    FROM raw.icon_d2_ruc_field AS field_row
    WHERE field_row.run_time_utc = p_run_time_utc
      AND field_row.lead_time = p_lead_time
      AND field_row.indicator = 'T_2M';

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
;

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
        MIN(source_row.retained_point_count)::BIGINT AS mask_cell_count
    FROM raw.icon_d2_ruc_source AS source_row
    WHERE source_row.run_time_utc = p_run_time_utc
      AND source_row.lead_time = p_lead_time
),
weather_rows AS (
    SELECT
        weather_row.cell_index,
        weather_row.temperature_c
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
        (SELECT COUNT(*) FROM weather_rows)::BIGINT AS normalized_row_count,
        (SELECT COUNT(*) FROM bridge_cells)::BIGINT AS bridge_cell_count,
        (
            SELECT COUNT(*)
            FROM mask_cells AS mask_cell
            WHERE NOT EXISTS (
                SELECT 1 FROM weather_rows AS weather_row
                WHERE weather_row.cell_index = mask_cell.cell_index
            )
        )::BIGINT AS missing_mask_cell_count,
        (
            SELECT COUNT(*)
            FROM weather_rows AS weather_row
            WHERE NOT EXISTS (
                SELECT 1 FROM mask_cells AS mask_cell
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
        )::BIGINT AS bridge_missing_value_count,
        (
            SELECT COUNT(*)
            FROM normalized.weather_partition_rejected AS rejected_row
            WHERE rejected_row.run_time_utc = p_run_time_utc
              AND rejected_row.lead_time = p_lead_time
        )::BIGINT AS rejected_partition_count
),
conversion_mismatches AS (
    SELECT COUNT(*)::BIGINT AS conversion_mismatch_count
    FROM weather_rows AS weather_row
    LEFT JOIN raw.icon_d2_ruc_field AS temperature_row
      ON temperature_row.run_time_utc = p_run_time_utc
     AND temperature_row.lead_time = p_lead_time
     AND temperature_row.indicator = 'T_2M'
     AND temperature_row.cell_index = weather_row.cell_index
    WHERE weather_row.temperature_c
        IS DISTINCT FROM temperature_row.source_value - 273.15
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

CREATE OR REPLACE FUNCTION analytical.refresh_plr_weather(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT
)
RETURNS TABLE (
    accepted BOOLEAN,
    plr_row_count BIGINT,
    expected_plr_count BIGINT,
    source_grid_id TEXT,
    geography_version TEXT,
    rejection_reason TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_valid_time_utc TIMESTAMPTZ;
    v_source_grid_id TEXT;
    v_geography_version TEXT;
    v_scope_variant_count BIGINT;
    v_expected_plr_count BIGINT;
    v_row_count BIGINT;
    v_normalized_quality RECORD;
BEGIN
    SELECT
        MIN(weather_row.valid_time_utc),
        MIN(weather_row.source_grid_id),
        MIN(weather_row.geography_version),
        (
            COUNT(DISTINCT weather_row.valid_time_utc)
            + COUNT(DISTINCT weather_row.source_grid_id)
            + COUNT(DISTINCT weather_row.geography_version)
        )::BIGINT
    INTO
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        v_scope_variant_count
    FROM normalized.icon_d2_ruc_weather AS weather_row
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time;

    IF v_scope_variant_count <> 3 THEN
        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            v_source_grid_id,
            v_geography_version,
            'normalized_weather_scope_incomplete_or_inconsistent'::TEXT;
        RETURN;
    END IF;

    SELECT *
    INTO v_normalized_quality
    FROM normalized.check_icon_d2_ruc_weather_quality(
        p_run_time_utc,
        p_lead_time
    );

    IF v_normalized_quality IS NULL
       OR NOT v_normalized_quality.passed THEN
        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            v_source_grid_id,
            v_geography_version,
            'normalized_weather_quality_failed'::TEXT;
        RETURN;
    END IF;

    SELECT
        COUNT(*)::BIGINT
    INTO v_expected_plr_count
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = v_geography_version;

    DELETE FROM analytical.plr_weather AS plr_weather_row
    WHERE plr_weather_row.run_time_utc = p_run_time_utc
      AND plr_weather_row.lead_time = p_lead_time;

    INSERT INTO analytical.plr_weather (
        plr_id,
        run_time_utc,
        lead_time,
        valid_time_utc,
        source_grid_id,
        geography_version,
        temperature_c
    )
    SELECT
        bridge_row.plr_id,
        p_run_time_utc,
        p_lead_time,
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        SUM(
            weather_row.temperature_c
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr)
    FROM normalized.icon_plr_area_bridge AS bridge_row
    JOIN normalized.icon_d2_ruc_weather AS weather_row
      ON weather_row.run_time_utc = p_run_time_utc
     AND weather_row.lead_time = p_lead_time
     AND weather_row.source_grid_id = bridge_row.source_grid_id
     AND weather_row.geography_version = bridge_row.geography_version
     AND weather_row.cell_index = bridge_row.cell_index
    WHERE bridge_row.source_grid_id = v_source_grid_id
      AND bridge_row.geography_version = v_geography_version
    GROUP BY
        bridge_row.plr_id,
        bridge_row.geography_version;

    GET DIAGNOSTICS v_row_count = ROW_COUNT;

    IF v_row_count <> v_expected_plr_count THEN
        DELETE FROM analytical.plr_weather AS plr_weather_row
        WHERE plr_weather_row.run_time_utc = p_run_time_utc
          AND plr_weather_row.lead_time = p_lead_time;

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            v_expected_plr_count,
            v_source_grid_id,
            v_geography_version,
            'plr_weather_row_count_mismatch'::TEXT;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        TRUE,
        v_row_count,
        v_expected_plr_count,
        v_source_grid_id,
        v_geography_version,
        NULL::TEXT;
END;
$$;


CREATE OR REPLACE FUNCTION analytical.check_plr_weather_quality(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    plr_row_count BIGINT,
    source_plr_count BIGINT,
    missing_plr_count BIGINT,
    null_metric_row_count BIGINT,
    normalized_weather_passed BOOLEAN
)
LANGUAGE sql
STABLE
AS $$
WITH weather_scope AS (
    SELECT
        MIN(plr_weather_row.geography_version) AS geography_version
    FROM analytical.plr_weather AS plr_weather_row
    WHERE plr_weather_row.run_time_utc = p_run_time_utc
      AND plr_weather_row.lead_time = p_lead_time
),
plr_weather_rows AS (
    SELECT
        plr_weather_row.plr_id,
        plr_weather_row.temperature_c
    FROM analytical.plr_weather AS plr_weather_row
    WHERE plr_weather_row.run_time_utc = p_run_time_utc
      AND plr_weather_row.lead_time = p_lead_time
),
source_plrs AS (
    SELECT plr_row.plr_id
    FROM normalized.plr AS plr_row
    CROSS JOIN weather_scope
    WHERE plr_row.geography_version = weather_scope.geography_version
),
normalized_quality AS (
    SELECT quality.passed
    FROM normalized.check_icon_d2_ruc_weather_quality(
        p_run_time_utc,
        p_lead_time
    ) AS quality
),
counts AS (
    SELECT
        (SELECT COUNT(*) FROM plr_weather_rows)::BIGINT
            AS plr_row_count,
        (SELECT COUNT(*) FROM source_plrs)::BIGINT
            AS source_plr_count,
        (
            SELECT COUNT(*)
            FROM source_plrs AS source_plr
            WHERE NOT EXISTS (
                SELECT 1
                FROM plr_weather_rows AS weather_row
                WHERE weather_row.plr_id = source_plr.plr_id
            )
        )::BIGINT AS missing_plr_count,
        (
            SELECT COUNT(*)
            FROM plr_weather_rows AS weather_row
            WHERE weather_row.temperature_c IS NULL
        )::BIGINT AS null_metric_row_count
)
SELECT
    (
        counts.plr_row_count = p_expected_plr_count
        AND counts.source_plr_count = p_expected_plr_count
        AND counts.missing_plr_count = 0
        AND counts.null_metric_row_count = 0
        AND COALESCE(normalized_quality.passed, FALSE)
    ) AS passed,
    counts.plr_row_count,
    counts.source_plr_count,
    counts.missing_plr_count,
    counts.null_metric_row_count,
    COALESCE(normalized_quality.passed, FALSE)
FROM counts
LEFT JOIN normalized_quality
  ON TRUE;
$$;

CREATE OR REPLACE FUNCTION analytical.refresh_plr_weather_population(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT
)
RETURNS TABLE (
    accepted BOOLEAN,
    final_row_count BIGINT,
    available_population_count BIGINT,
    rejected_population_count BIGINT,
    population_reference_date DATE,
    rejection_reason TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_weather_quality RECORD;
    v_geography_version TEXT;
    v_population_reference_date DATE;
    v_population_source_count BIGINT;
    v_exception_count BIGINT;
    v_final_row_count BIGINT;
    v_available_count BIGINT;
    v_rejected_count BIGINT;
BEGIN
    SELECT *
    INTO v_weather_quality
    FROM analytical.check_plr_weather_quality(
        p_run_time_utc,
        p_lead_time
    );

    IF v_weather_quality IS NULL
       OR NOT v_weather_quality.passed THEN
        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            NULL::DATE,
            'plr_weather_quality_failed'::TEXT;
        RETURN;
    END IF;

    SELECT
        MIN(weather_row.geography_version)
    INTO v_geography_version
    FROM analytical.plr_weather AS weather_row
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time;

    SELECT
        MAX(population_row.reference_date)
    INTO v_population_reference_date
    FROM (
        SELECT accepted.reference_date
        FROM normalized.plr_population_65plus AS accepted
        UNION ALL
        SELECT rejected.reference_date
        FROM normalized.plr_population_rejected AS rejected
    ) AS population_row;

    IF v_population_reference_date IS NULL THEN
        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            NULL::DATE,
            'no_population_snapshot_available'::TEXT;
        RETURN;
    END IF;

    SELECT
        COUNT(DISTINCT population_source.source_sha256)::BIGINT
    INTO v_population_source_count
    FROM (
        SELECT accepted.source_sha256
        FROM normalized.plr_population_65plus AS accepted
        WHERE accepted.reference_date = v_population_reference_date
        UNION ALL
        SELECT rejected.source_sha256
        FROM normalized.plr_population_rejected AS rejected
        WHERE rejected.reference_date = v_population_reference_date
    ) AS population_source;

    IF v_population_source_count <> 1 THEN
        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            v_population_reference_date,
            'population_snapshot_source_inconsistent'::TEXT;
        RETURN;
    END IF;

    DELETE FROM analytical.plr_weather_population AS final_row
    WHERE final_row.run_time_utc = p_run_time_utc
      AND final_row.lead_time = p_lead_time;

    DELETE FROM analytical.plr_weather_population_rejected AS rejected_row
    WHERE rejected_row.run_time_utc = p_run_time_utc
      AND rejected_row.lead_time = p_lead_time;

    WITH classification AS (
        SELECT
            weather_row.plr_id,
            weather_row.geography_version,
            CASE
                WHEN accepted.plr_id IS NOT NULL THEN 1
                ELSE 0
            END
            + CASE
                WHEN rejected.plr_id IS NOT NULL THEN 1
                ELSE 0
            END AS population_match_count
        FROM analytical.plr_weather AS weather_row
        LEFT JOIN normalized.plr_population_65plus AS accepted
          ON accepted.plr_id = weather_row.plr_id
         AND accepted.reference_date = v_population_reference_date
        LEFT JOIN normalized.plr_population_rejected AS rejected
          ON rejected.plr_id = weather_row.plr_id
         AND rejected.reference_date = v_population_reference_date
        WHERE weather_row.run_time_utc = p_run_time_utc
          AND weather_row.lead_time = p_lead_time
    )
    SELECT
        COUNT(*)::BIGINT
    INTO v_exception_count
    FROM classification
    WHERE population_match_count <> 1;

    IF v_exception_count > 0 THEN
        INSERT INTO analytical.plr_weather_population_rejected (
            run_time_utc,
            lead_time,
            plr_id,
            geography_version,
            population_reference_date,
            rejection_reason,
            rejection_details
        )
        SELECT
            p_run_time_utc,
            p_lead_time,
            weather_row.plr_id,
            weather_row.geography_version,
            v_population_reference_date,
            CASE
                WHEN accepted.plr_id IS NULL
                 AND rejected.plr_id IS NULL
                    THEN 'population_record_missing'
                ELSE 'population_accept_reject_overlap'
            END,
            jsonb_build_object(
                'accepted_present',
                accepted.plr_id IS NOT NULL,
                'rejected_present',
                rejected.plr_id IS NOT NULL
            )
        FROM analytical.plr_weather AS weather_row
        LEFT JOIN normalized.plr_population_65plus AS accepted
          ON accepted.plr_id = weather_row.plr_id
         AND accepted.reference_date = v_population_reference_date
        LEFT JOIN normalized.plr_population_rejected AS rejected
          ON rejected.plr_id = weather_row.plr_id
         AND rejected.reference_date = v_population_reference_date
        WHERE weather_row.run_time_utc = p_run_time_utc
          AND weather_row.lead_time = p_lead_time
          AND (
                CASE
                    WHEN accepted.plr_id IS NOT NULL THEN 1
                    ELSE 0
                END
                + CASE
                    WHEN rejected.plr_id IS NOT NULL THEN 1
                    ELSE 0
                END
              ) <> 1;

        RETURN QUERY
        SELECT
            FALSE,
            0::BIGINT,
            0::BIGINT,
            0::BIGINT,
            v_population_reference_date,
            'population_join_exceptions_detected'::TEXT;
        RETURN;
    END IF;

    INSERT INTO analytical.plr_weather_population (
        plr_id,
        run_time_utc,
        lead_time,
        valid_time_utc,
        source_grid_id,
        geography_version,
        temperature_c,
        population_total,
        population_65plus,
        share_65plus,
        population_reference_date,
        population_publication_date,
        population_source_sha256,
        population_status,
        population_rejection_reason
    )
    SELECT
        weather_row.plr_id,
        weather_row.run_time_utc,
        weather_row.lead_time,
        weather_row.valid_time_utc,
        weather_row.source_grid_id,
        weather_row.geography_version,
        weather_row.temperature_c,
        accepted.population_total,
        accepted.population_65plus,
        accepted.share_65plus,
        COALESCE(accepted.reference_date, rejected.reference_date),
        COALESCE(accepted.publication_date, rejected.publication_date),
        COALESCE(accepted.source_sha256, rejected.source_sha256),
        CASE
            WHEN accepted.plr_id IS NOT NULL THEN 'available'
            ELSE 'rejected_source_record'
        END,
        rejected.rejection_reason
    FROM analytical.plr_weather AS weather_row
    LEFT JOIN normalized.plr_population_65plus AS accepted
      ON accepted.plr_id = weather_row.plr_id
     AND accepted.reference_date = v_population_reference_date
    LEFT JOIN normalized.plr_population_rejected AS rejected
      ON rejected.plr_id = weather_row.plr_id
     AND rejected.reference_date = v_population_reference_date
    WHERE weather_row.run_time_utc = p_run_time_utc
      AND weather_row.lead_time = p_lead_time;

    GET DIAGNOSTICS v_final_row_count = ROW_COUNT;

    SELECT
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'available'
        )::BIGINT,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'rejected_source_record'
        )::BIGINT
    INTO
        v_available_count,
        v_rejected_count
    FROM analytical.plr_weather_population AS final_row
    WHERE final_row.run_time_utc = p_run_time_utc
      AND final_row.lead_time = p_lead_time;

    RETURN QUERY
    SELECT
        TRUE,
        v_final_row_count,
        v_available_count,
        v_rejected_count,
        v_population_reference_date,
        NULL::TEXT;
END;
$$;


CREATE OR REPLACE FUNCTION analytical.check_plr_weather_population_quality(
    p_run_time_utc TIMESTAMPTZ,
    p_lead_time TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    final_row_count BIGINT,
    available_count BIGINT,
    rejected_source_record_count BIGINT,
    available_missing_population_metric_count BIGINT,
    rejected_with_population_metric_count BIGINT,
    rejected_missing_reason_count BIGINT,
    available_with_rejection_reason_count BIGINT,
    rejected_registry_mismatch_count BIGINT,
    analytical_rejection_count BIGINT,
    plr_weather_passed BOOLEAN
)
LANGUAGE sql
STABLE
AS $$
WITH final_rows AS (
    SELECT
        final_row.plr_id,
        final_row.population_reference_date,
        final_row.population_status,
        final_row.population_total,
        final_row.population_65plus,
        final_row.share_65plus,
        final_row.population_rejection_reason
    FROM analytical.plr_weather_population AS final_row
    WHERE final_row.run_time_utc = p_run_time_utc
      AND final_row.lead_time = p_lead_time
),
population_scope AS (
    SELECT
        MIN(final_row.population_reference_date)
            AS reference_date
    FROM final_rows AS final_row
),
weather_quality AS (
    SELECT quality.passed
    FROM analytical.check_plr_weather_quality(
        p_run_time_utc,
        p_lead_time,
        p_expected_plr_count
    ) AS quality
),
counts AS (
    SELECT
        COUNT(*)::BIGINT AS final_row_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'available'
        )::BIGINT AS available_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'rejected_source_record'
        )::BIGINT AS rejected_source_record_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'available'
              AND (
                    final_row.population_total IS NULL
                 OR final_row.population_65plus IS NULL
                 OR final_row.share_65plus IS NULL
              )
        )::BIGINT AS available_missing_population_metric_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'rejected_source_record'
              AND (
                    final_row.population_total IS NOT NULL
                 OR final_row.population_65plus IS NOT NULL
                 OR final_row.share_65plus IS NOT NULL
              )
        )::BIGINT AS rejected_with_population_metric_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'rejected_source_record'
              AND final_row.population_rejection_reason IS NULL
        )::BIGINT AS rejected_missing_reason_count,
        COUNT(*) FILTER (
            WHERE final_row.population_status = 'available'
              AND final_row.population_rejection_reason IS NOT NULL
        )::BIGINT AS available_with_rejection_reason_count
    FROM final_rows AS final_row
),
registry_mismatch AS (
    SELECT
        (
            (
                SELECT COUNT(*)
                FROM (
                    SELECT final_row.plr_id
                    FROM final_rows AS final_row
                    WHERE final_row.population_status
                        = 'rejected_source_record'
                    EXCEPT
                    SELECT rejected.plr_id
                    FROM normalized.plr_population_rejected AS rejected
                    CROSS JOIN population_scope
                    WHERE rejected.reference_date
                        = population_scope.reference_date
                ) AS final_only
            )
            +
            (
                SELECT COUNT(*)
                FROM (
                    SELECT rejected.plr_id
                    FROM normalized.plr_population_rejected AS rejected
                    CROSS JOIN population_scope
                    WHERE rejected.reference_date
                        = population_scope.reference_date
                    EXCEPT
                    SELECT final_row.plr_id
                    FROM final_rows AS final_row
                    WHERE final_row.population_status
                        = 'rejected_source_record'
                ) AS registry_only
            )
        )::BIGINT AS rejected_registry_mismatch_count
),
analytical_rejections AS (
    SELECT
        COUNT(*)::BIGINT AS analytical_rejection_count
    FROM analytical.plr_weather_population_rejected AS rejected_row
    WHERE rejected_row.run_time_utc = p_run_time_utc
      AND rejected_row.lead_time = p_lead_time
)
SELECT
    (
        counts.final_row_count = p_expected_plr_count
        AND counts.available_count
            + counts.rejected_source_record_count
            = p_expected_plr_count
        AND counts.available_missing_population_metric_count = 0
        AND counts.rejected_with_population_metric_count = 0
        AND counts.rejected_missing_reason_count = 0
        AND counts.available_with_rejection_reason_count = 0
        AND registry_mismatch.rejected_registry_mismatch_count = 0
        AND analytical_rejections.analytical_rejection_count = 0
        AND COALESCE(weather_quality.passed, FALSE)
    ) AS passed,
    counts.final_row_count,
    counts.available_count,
    counts.rejected_source_record_count,
    counts.available_missing_population_metric_count,
    counts.rejected_with_population_metric_count,
    counts.rejected_missing_reason_count,
    counts.available_with_rejection_reason_count,
    registry_mismatch.rejected_registry_mismatch_count,
    analytical_rejections.analytical_rejection_count,
    COALESCE(weather_quality.passed, FALSE)
FROM counts
CROSS JOIN registry_mismatch
CROSS JOIN analytical_rejections
LEFT JOIN weather_quality
  ON TRUE;
$$;

DROP VIEW IF EXISTS analytical.current_plr_weather_population;

ALTER TABLE normalized.icon_d2_ruc_weather
    DROP COLUMN IF EXISTS relative_humidity_percent,
    DROP COLUMN IF EXISTS dew_point_temperature_c,
    DROP COLUMN IF EXISTS wind_u_10m_ms,
    DROP COLUMN IF EXISTS wind_v_10m_ms;

ALTER TABLE analytical.plr_weather
    DROP COLUMN IF EXISTS relative_humidity_percent,
    DROP COLUMN IF EXISTS dew_point_temperature_c,
    DROP COLUMN IF EXISTS wind_u_10m_ms,
    DROP COLUMN IF EXISTS wind_v_10m_ms,
    DROP COLUMN IF EXISTS wind_speed_10m_ms;

ALTER TABLE analytical.plr_weather_population
    DROP COLUMN IF EXISTS relative_humidity_percent,
    DROP COLUMN IF EXISTS dew_point_temperature_c,
    DROP COLUMN IF EXISTS wind_u_10m_ms,
    DROP COLUMN IF EXISTS wind_v_10m_ms,
    DROP COLUMN IF EXISTS wind_speed_10m_ms;

CREATE VIEW analytical.current_plr_weather_population AS
WITH latest_partition AS (
    SELECT
        final_row.run_time_utc,
        final_row.lead_time,
        MIN(final_row.valid_time_utc) AS valid_time_utc
    FROM analytical.plr_weather_population AS final_row
    GROUP BY
        final_row.run_time_utc,
        final_row.lead_time
    ORDER BY
        final_row.run_time_utc DESC,
        valid_time_utc ASC
    LIMIT 1
)
SELECT final_row.*
FROM analytical.plr_weather_population AS final_row
JOIN latest_partition AS latest
  ON latest.run_time_utc = final_row.run_time_utc
 AND latest.lead_time = final_row.lead_time;

COMMIT;
