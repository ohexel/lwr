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
        temperature_c,
        relative_humidity_percent,
        dew_point_temperature_c,
        wind_u_10m_ms,
        wind_v_10m_ms,
        wind_speed_10m_ms
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
        ) / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.relative_humidity_percent
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.dew_point_temperature_c
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.wind_u_10m_ms
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.wind_v_10m_ms
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr),
        SUM(
            SQRT(
                POWER(weather_row.wind_u_10m_ms, 2)
                + POWER(weather_row.wind_v_10m_ms, 2)
            )
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
    SELECT plr_weather_row.*
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
               OR weather_row.relative_humidity_percent IS NULL
               OR weather_row.dew_point_temperature_c IS NULL
               OR weather_row.wind_u_10m_ms IS NULL
               OR weather_row.wind_v_10m_ms IS NULL
               OR weather_row.wind_speed_10m_ms IS NULL
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
