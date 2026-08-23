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
        relative_humidity_percent,
        dew_point_temperature_c,
        wind_u_10m_ms,
        wind_v_10m_ms,
        wind_speed_10m_ms,
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
        weather_row.relative_humidity_percent,
        weather_row.dew_point_temperature_c,
        weather_row.wind_u_10m_ms,
        weather_row.wind_v_10m_ms,
        weather_row.wind_speed_10m_ms,
        accepted.population_total,
        accepted.population_65plus,
        accepted.share_65plus,
        COALESCE(
            accepted.reference_date,
            rejected.reference_date
        ),
        COALESCE(
            accepted.publication_date,
            rejected.publication_date
        ),
        COALESCE(
            accepted.source_sha256,
            rejected.source_sha256
        ),
        CASE
            WHEN accepted.plr_id IS NOT NULL
                THEN 'available'
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
    SELECT final_row.*
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
