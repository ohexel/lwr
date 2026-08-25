--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9 (Debian 16.9-1.pgdg110+1)
-- Dumped by pg_dump version 16.9 (Debian 16.9-1.pgdg110+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: analytical; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA analytical;


--
-- Name: normalized; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA normalized;


--
-- Name: raw; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA raw;


--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: check_hostrada_month_quality(date, text, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.check_hostrada_month_quality(p_source_month_utc date, p_geography_version text, p_source_grid_id text) RETURNS TABLE(passed boolean, source_file_count bigint, expected_hour_count bigint, expected_plr_count bigint, plr_hour_count bigint, berlin_hour_count bigint, incomplete_plr_hour_count bigint, missing_berlin_hour_count bigint)
    LANGUAGE sql STABLE
    AS $$
    WITH bounds AS (
        SELECT
            p_source_month_utc::TIMESTAMP
                AT TIME ZONE 'UTC' AS start_utc,
            (
                p_source_month_utc::TIMESTAMP + INTERVAL '1 month'
            ) AT TIME ZONE 'UTC' AS end_utc
    ),
    expected_hours AS (
        SELECT generated_hour.valid_time_utc
        FROM bounds
        CROSS JOIN LATERAL generate_series(
            bounds.start_utc,
            bounds.end_utc - INTERVAL '1 hour',
            INTERVAL '1 hour'
        ) AS generated_hour(valid_time_utc)
    ),
    plr_counts AS (
        SELECT
            hourly_row.valid_time_utc,
            COUNT(*)::BIGINT AS row_count
        FROM analytical.hostrada_plr_hourly AS hourly_row
        WHERE hourly_row.source_month_utc = p_source_month_utc
          AND hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
        GROUP BY hourly_row.valid_time_utc
    ),
    totals AS (
        SELECT
            (
                SELECT COUNT(*)::BIGINT
                FROM raw.hostrada_month_source AS source_row
                WHERE source_row.source_month_utc = p_source_month_utc
                  AND source_row.source_grid_id = p_source_grid_id
            ) AS files,
            (SELECT COUNT(*)::BIGINT FROM expected_hours) AS hours,
            (
                SELECT COUNT(*)::BIGINT
                FROM normalized.plr AS plr_row
                WHERE plr_row.geography_version = p_geography_version
            ) AS plrs,
            (
                SELECT COUNT(*)::BIGINT
                FROM analytical.hostrada_plr_hourly AS hourly_row
                WHERE hourly_row.source_month_utc = p_source_month_utc
                  AND hourly_row.geography_version = p_geography_version
                  AND hourly_row.source_grid_id = p_source_grid_id
            ) AS plr_rows,
            (
                SELECT COUNT(*)::BIGINT
                FROM analytical.hostrada_berlin_hourly AS hourly_row
                WHERE hourly_row.source_month_utc = p_source_month_utc
                  AND hourly_row.geography_version = p_geography_version
                  AND hourly_row.source_grid_id = p_source_grid_id
            ) AS berlin_rows
    ),
    hourly_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE COALESCE(plr_counts.row_count, 0) <> totals.plrs
            )::BIGINT AS incomplete_plr_hours,
            COUNT(*) FILTER (
                WHERE berlin_row.valid_time_utc IS NULL
            )::BIGINT AS missing_berlin_hours
        FROM expected_hours AS expected_hour
        CROSS JOIN totals
        LEFT JOIN plr_counts
          ON plr_counts.valid_time_utc = expected_hour.valid_time_utc
        LEFT JOIN analytical.hostrada_berlin_hourly AS berlin_row
          ON berlin_row.source_month_utc = p_source_month_utc
         AND berlin_row.valid_time_utc = expected_hour.valid_time_utc
         AND berlin_row.geography_version = p_geography_version
         AND berlin_row.source_grid_id = p_source_grid_id
    )
    SELECT
        totals.files = 3
            AND totals.plrs > 0
            AND totals.plr_rows = totals.hours * totals.plrs
            AND totals.berlin_rows = totals.hours
            AND hourly_quality.incomplete_plr_hours = 0
            AND hourly_quality.missing_berlin_hours = 0,
        totals.files,
        totals.hours,
        totals.plrs,
        totals.plr_rows,
        totals.berlin_rows,
        hourly_quality.incomplete_plr_hours,
        hourly_quality.missing_berlin_hours
    FROM totals
    CROSS JOIN hourly_quality;
$$;


--
-- Name: check_hostrada_reference_month_quality(integer, text, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.check_hostrada_reference_month_quality(p_calendar_month integer, p_geography_version text, p_source_grid_id text) RETURNS TABLE(passed boolean, expected_plr_count bigint, expected_calendar_hour_count bigint, expected_observation_count bigint, source_month_count bigint, source_month_failure_count bigint, plr_reference_count bigint, berlin_reference_count bigint, plr_sample_count_failure_count bigint, berlin_sample_count_failure_count bigint)
    LANGUAGE sql STABLE
    AS $$
    WITH expected_hours AS MATERIALIZED (
        SELECT *
        FROM analytical.hostrada_reference_expected_hours(
            p_calendar_month
        )
    ),
    source_months AS MATERIALIZED (
        SELECT DISTINCT source_window.source_month_utc
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
    ),
    source_quality AS (
        SELECT
            COUNT(*)::BIGINT AS month_count,
            COUNT(*) FILTER (
                WHERE source_manifest.source_file_count <> 3
                   OR source_manifest.variable_count <> 3
            )::BIGINT AS failure_count
        FROM source_months AS source_month
        CROSS JOIN LATERAL (
            SELECT
                COUNT(*)::BIGINT AS source_file_count,
                COUNT(DISTINCT source_row.variable_name)::BIGINT
                    AS variable_count
            FROM raw.hostrada_month_source AS source_row
            WHERE source_row.source_month_utc
                = source_month.source_month_utc
              AND source_row.source_grid_id = p_source_grid_id
        ) AS source_manifest
    ),
    expected_totals AS (
        SELECT
            (
                SELECT COUNT(*)::BIGINT
                FROM normalized.plr AS plr_row
                WHERE plr_row.geography_version = p_geography_version
            ) AS plr_count,
            COUNT(*)::BIGINT AS hour_count,
            COALESCE(SUM(expected_hour.sample_count), 0)::BIGINT
                AS observation_count
        FROM expected_hours AS expected_hour
    ),
    plr_quality AS (
        SELECT
            COUNT(*)::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE expected_hour.sample_count IS DISTINCT FROM
                    reference_row.sample_count
            )::BIGINT AS sample_failure_count
        FROM analytical.hostrada_plr_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
        WHERE reference_row.calendar_month = p_calendar_month
          AND reference_row.geography_version = p_geography_version
    ),
    berlin_quality AS (
        SELECT
            COUNT(*)::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE expected_hour.sample_count IS DISTINCT FROM
                    reference_row.sample_count
            )::BIGINT AS sample_failure_count
        FROM analytical.hostrada_berlin_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
        WHERE reference_row.calendar_month = p_calendar_month
          AND reference_row.geography_version = p_geography_version
    )
    SELECT
        p_calendar_month BETWEEN 1 AND 12
            AND expected.plr_count > 0
            AND expected.hour_count > 0
            AND source.month_count > 0
            AND source.failure_count = 0
            AND plr.row_count = expected.hour_count * expected.plr_count
            AND berlin.row_count = expected.hour_count
            AND plr.sample_failure_count = 0
            AND berlin.sample_failure_count = 0,
        expected.plr_count,
        expected.hour_count,
        expected.observation_count,
        source.month_count,
        source.failure_count,
        plr.row_count,
        berlin.row_count,
        plr.sample_failure_count,
        berlin.sample_failure_count
    FROM expected_totals AS expected
    CROSS JOIN source_quality AS source
    CROSS JOIN plr_quality AS plr
    CROSS JOIN berlin_quality AS berlin;
$$;


--
-- Name: check_plr_weather_population_quality(timestamp with time zone, text, integer); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.check_plr_weather_population_quality(p_run_time_utc timestamp with time zone, p_lead_time text, p_expected_plr_count integer DEFAULT 542) RETURNS TABLE(passed boolean, final_row_count bigint, available_count bigint, rejected_source_record_count bigint, available_missing_population_metric_count bigint, rejected_with_population_metric_count bigint, rejected_missing_reason_count bigint, available_with_rejection_reason_count bigint, rejected_registry_mismatch_count bigint, analytical_rejection_count bigint, plr_weather_passed boolean)
    LANGUAGE sql STABLE
    AS $$
WITH final_rows AS (
    SELECT
        final_row.plr_id,
        final_row.temperature_c,
        final_row.apparent_temperature_shade_c,
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
            WHERE final_row.temperature_c IS NULL
               OR final_row.apparent_temperature_shade_c IS NULL
        )::BIGINT AS missing_weather_metric_count,
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
        AND counts.missing_weather_metric_count = 0
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


--
-- Name: check_plr_weather_quality(timestamp with time zone, text, integer); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.check_plr_weather_quality(p_run_time_utc timestamp with time zone, p_lead_time text, p_expected_plr_count integer DEFAULT 542) RETURNS TABLE(passed boolean, plr_row_count bigint, source_plr_count bigint, missing_plr_count bigint, null_metric_row_count bigint, normalized_weather_passed boolean)
    LANGUAGE sql STABLE
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
        plr_weather_row.temperature_c,
        plr_weather_row.apparent_temperature_shade_c
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
               OR weather_row.apparent_temperature_shade_c IS NULL
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


--
-- Name: hostrada_reference_expected_hours(integer); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.hostrada_reference_expected_hours(p_calendar_month integer) RETURNS TABLE(calendar_month smallint, calendar_day smallint, local_hour smallint, sample_count smallint)
    LANGUAGE sql STABLE ROWS 744
    AS $$
    WITH local_hours AS (
        SELECT
            generated_hour.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        CROSS JOIN LATERAL generate_series(
            source_window.start_utc,
            source_window.end_utc - INTERVAL '1 hour',
            INTERVAL '1 hour'
        ) AS generated_hour(valid_time_utc)
    )
    SELECT
        EXTRACT(MONTH FROM local_hour.valid_time_berlin)::SMALLINT,
        EXTRACT(DAY FROM local_hour.valid_time_berlin)::SMALLINT,
        EXTRACT(HOUR FROM local_hour.valid_time_berlin)::SMALLINT,
        COUNT(*)::SMALLINT
    FROM local_hours AS local_hour
    WHERE NOT (
        EXTRACT(MONTH FROM local_hour.valid_time_berlin) = 2
        AND EXTRACT(DAY FROM local_hour.valid_time_berlin) = 29
    )
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3;
$$;


--
-- Name: hostrada_reference_source_windows(integer); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.hostrada_reference_source_windows(p_calendar_month integer) RETURNS TABLE(reference_year integer, source_month_utc date, start_utc timestamp with time zone, end_utc timestamp with time zone)
    LANGUAGE sql STABLE ROWS 64
    AS $$
    WITH local_month_bounds AS (
        SELECT
            years.reference_year,
            make_timestamptz(
                years.reference_year,
                p_calendar_month,
                1,
                0,
                0,
                0,
                'Europe/Berlin'
            ) AS local_start_utc,
            (
                make_date(years.reference_year, p_calendar_month, 1)
                    + INTERVAL '1 month'
            )::TIMESTAMP AT TIME ZONE 'Europe/Berlin'
                AS local_end_utc
        FROM generate_series(1995, 2025) AS years(reference_year)
        WHERE p_calendar_month BETWEEN 1 AND 12
    ),
    candidate_source_months AS (
        SELECT
            local_bounds.reference_year,
            candidate.source_month_utc,
            local_bounds.local_start_utc,
            local_bounds.local_end_utc
        FROM local_month_bounds AS local_bounds
        CROSS JOIN LATERAL (
            SELECT DATE_TRUNC(
                'month',
                local_bounds.local_start_utc AT TIME ZONE 'UTC'
            )::DATE AS source_month_utc

            UNION

            SELECT DATE_TRUNC(
                'month',
                (
                    local_bounds.local_end_utc - INTERVAL '1 hour'
                ) AT TIME ZONE 'UTC'
            )::DATE AS source_month_utc
        ) AS candidate
        WHERE candidate.source_month_utc >= DATE '1995-01-01'
          AND candidate.source_month_utc < DATE '2026-01-01'
    )
    SELECT
        candidate.reference_year,
        candidate.source_month_utc,
        GREATEST(
            candidate.local_start_utc,
            candidate.source_month_utc::TIMESTAMP AT TIME ZONE 'UTC'
        ) AS start_utc,
        LEAST(
            candidate.local_end_utc,
            (
                candidate.source_month_utc::TIMESTAMP + INTERVAL '1 month'
            ) AT TIME ZONE 'UTC'
        ) AS end_utc
    FROM candidate_source_months AS candidate
    ORDER BY
        candidate.reference_year,
        candidate.source_month_utc;
$$;


--
-- Name: refresh_hostrada_month(date, text, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.refresh_hostrada_month(p_source_month_utc date, p_geography_version text, p_source_grid_id text) RETURNS TABLE(source_cell_hour_count bigint, plr_hour_count bigint, berlin_hour_count bigint, expected_hour_count bigint, expected_cell_count bigint, expected_plr_count bigint)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_start_utc TIMESTAMPTZ;
    v_end_utc TIMESTAMPTZ;
    v_expected_hours BIGINT;
    v_expected_cells BIGINT;
    v_expected_plrs BIGINT;
    v_source_rows BIGINT;
    v_plr_rows BIGINT;
    v_berlin_rows BIGINT;
    v_bridge_passed BOOLEAN;
BEGIN
    IF p_source_month_utc IS NULL
       OR p_source_month_utc
            <> DATE_TRUNC('month', p_source_month_utc::TIMESTAMP)::DATE THEN
        RAISE EXCEPTION 'p_source_month_utc must be the first UTC month day';
    END IF;

    v_start_utc := p_source_month_utc::TIMESTAMP AT TIME ZONE 'UTC';
    v_end_utc := (
        p_source_month_utc::TIMESTAMP + INTERVAL '1 month'
    ) AT TIME ZONE 'UTC';
    v_expected_hours := (
        EXTRACT(EPOCH FROM (v_end_utc - v_start_utc)) / 3600
    )::BIGINT;

    SELECT COUNT(*)::BIGINT
    INTO v_expected_plrs
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    SELECT COUNT(*)::BIGINT
    INTO v_expected_cells
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;

    IF v_expected_plrs = 0 OR v_expected_cells = 0 THEN
        RAISE EXCEPTION
            'HOSTRADA geography % or source grid % is not materialized',
            p_geography_version,
            p_source_grid_id;
    END IF;

    SELECT bridge_quality.passed
    INTO v_bridge_passed
    FROM normalized.check_hostrada_plr_area_bridge_quality(
        p_geography_version,
        p_source_grid_id,
        v_expected_plrs::INTEGER
    ) AS bridge_quality;

    IF v_bridge_passed IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'HOSTRADA spatial bridge failed its quality check';
    END IF;

    IF (
        SELECT COUNT(*)
        FROM raw.hostrada_month_source AS source_row
        WHERE source_row.source_month_utc = p_source_month_utc
          AND source_row.source_grid_id = p_source_grid_id
          AND source_row.source_hour_count = v_expected_hours
          AND source_row.first_valid_time_utc = v_start_utc
          AND source_row.last_valid_time_utc
                = v_end_utc - INTERVAL '1 hour'
    ) <> 3 THEN
        RAISE EXCEPTION
            'HOSTRADA month % requires three validated source files',
            p_source_month_utc;
    END IF;

    SELECT COUNT(*)::BIGINT
    INTO v_source_rows
    FROM pg_temp.hostrada_cell_hour_stage AS stage_row;

    IF v_source_rows <> v_expected_hours * v_expected_cells THEN
        RAISE EXCEPTION
            'HOSTRADA staging has % rows; expected % hours x % cells',
            v_source_rows,
            v_expected_hours,
            v_expected_cells;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_temp.hostrada_cell_hour_stage AS stage_row
        LEFT JOIN normalized.hostrada_cell AS cell_row
          ON cell_row.source_grid_id = p_source_grid_id
         AND cell_row.geography_version = p_geography_version
         AND cell_row.y_index = stage_row.y_index
         AND cell_row.x_index = stage_row.x_index
        WHERE stage_row.valid_time_utc < v_start_utc
           OR stage_row.valid_time_utc >= v_end_utc
           OR cell_row.source_grid_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'HOSTRADA staging contains an unexpected hour or grid cell';
    END IF;

    -- Deletes and replacements share the caller's transaction. A failed rerun
    -- therefore rolls back to the last accepted version of both tables.
    DELETE FROM analytical.hostrada_plr_hourly AS hourly_row
    WHERE hourly_row.source_month_utc = p_source_month_utc;

    DELETE FROM analytical.hostrada_berlin_hourly AS hourly_row
    WHERE hourly_row.source_month_utc = p_source_month_utc;

    INSERT INTO analytical.hostrada_plr_hourly (
        source_month_utc,
        valid_time_utc,
        plr_id,
        geography_version,
        source_grid_id,
        temperature_c,
        apparent_temperature_shade_c
    )
    WITH cell_weather AS MATERIALIZED (
        SELECT
            stage_row.valid_time_utc,
            stage_row.y_index,
            stage_row.x_index,
            stage_row.temperature_c,
            normalized.calculate_apparent_temperature_shade_c(
                stage_row.temperature_c,
                stage_row.relative_humidity_percent,
                stage_row.wind_speed_10m_ms
            ) AS apparent_temperature_shade_c
        FROM pg_temp.hostrada_cell_hour_stage AS stage_row
    )
    SELECT
        p_source_month_utc,
        weather_row.valid_time_utc,
        bridge_row.plr_id,
        p_geography_version,
        p_source_grid_id,
        SUM(weather_row.temperature_c * bridge_row.fraction_of_plr)
            / SUM(bridge_row.fraction_of_plr),
        SUM(
            weather_row.apparent_temperature_shade_c
            * bridge_row.fraction_of_plr
        ) / SUM(bridge_row.fraction_of_plr)
    FROM cell_weather AS weather_row
    JOIN normalized.hostrada_plr_area_bridge AS bridge_row
      ON bridge_row.geography_version = p_geography_version
     AND bridge_row.source_grid_id = p_source_grid_id
     AND bridge_row.y_index = weather_row.y_index
     AND bridge_row.x_index = weather_row.x_index
    GROUP BY weather_row.valid_time_utc, bridge_row.plr_id;

    GET DIAGNOSTICS v_plr_rows = ROW_COUNT;

    IF v_plr_rows <> v_expected_hours * v_expected_plrs THEN
        RAISE EXCEPTION
            'HOSTRADA PLR output has % rows; expected % hours x % PLRs',
            v_plr_rows,
            v_expected_hours,
            v_expected_plrs;
    END IF;

    -- Berlin means the actual union of PLR areas, not the bounding box and not
    -- an unweighted mean of differently sized planning areas.
    INSERT INTO analytical.hostrada_berlin_hourly (
        source_month_utc,
        valid_time_utc,
        geography_version,
        source_grid_id,
        temperature_c,
        apparent_temperature_shade_c
    )
    SELECT
        p_source_month_utc,
        hourly_row.valid_time_utc,
        p_geography_version,
        p_source_grid_id,
        SUM(hourly_row.temperature_c * ST_Area(plr_row.geometry))
            / SUM(ST_Area(plr_row.geometry)),
        SUM(
            hourly_row.apparent_temperature_shade_c
            * ST_Area(plr_row.geometry)
        ) / SUM(ST_Area(plr_row.geometry))
    FROM analytical.hostrada_plr_hourly AS hourly_row
    JOIN normalized.plr AS plr_row
      ON plr_row.plr_id = hourly_row.plr_id
     AND plr_row.geography_version = p_geography_version
    WHERE hourly_row.source_month_utc = p_source_month_utc
      AND hourly_row.geography_version = p_geography_version
      AND hourly_row.source_grid_id = p_source_grid_id
    GROUP BY hourly_row.valid_time_utc;

    GET DIAGNOSTICS v_berlin_rows = ROW_COUNT;

    IF v_berlin_rows <> v_expected_hours THEN
        RAISE EXCEPTION
            'HOSTRADA Berlin output has % rows; expected %',
            v_berlin_rows,
            v_expected_hours;
    END IF;

    RETURN QUERY
    SELECT
        v_source_rows,
        v_plr_rows,
        v_berlin_rows,
        v_expected_hours,
        v_expected_cells,
        v_expected_plrs;
END;
$$;


--
-- Name: refresh_hostrada_reference_month(integer, text, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.refresh_hostrada_reference_month(p_calendar_month integer, p_geography_version text, p_source_grid_id text) RETURNS TABLE(calendar_month smallint, expected_plr_count bigint, expected_calendar_hour_count bigint, expected_observation_count bigint, plr_reference_count bigint, berlin_reference_count bigint)
    LANGUAGE plpgsql
    SET work_mem TO '64MB'
    AS $$
DECLARE
    v_expected_plrs BIGINT;
    v_expected_source_months BIGINT;
    v_source_files BIGINT;
    v_quality RECORD;
BEGIN
    IF p_calendar_month IS NULL
       OR p_calendar_month NOT BETWEEN 1 AND 12 THEN
        RAISE EXCEPTION 'Calendar month must be between 1 and 12';
    END IF;

    IF p_geography_version IS NULL OR p_source_grid_id IS NULL THEN
        RAISE EXCEPTION 'Geography version and HOSTRADA grid are required';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext('analytical.hostrada_hourly_reference'),
        p_calendar_month
    );

    SELECT COUNT(*)::BIGINT
    INTO v_expected_plrs
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    IF v_expected_plrs = 0 THEN
        RAISE EXCEPTION 'No PLRs exist for geography %', p_geography_version;
    END IF;

    WITH required_source_months AS (
        SELECT DISTINCT source_window.source_month_utc
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
    )
    SELECT
        COUNT(DISTINCT required_month.source_month_utc)::BIGINT,
        COUNT(source_row.variable_name)::BIGINT
    INTO
        v_expected_source_months,
        v_source_files
    FROM required_source_months AS required_month
    LEFT JOIN raw.hostrada_month_source AS source_row
      ON source_row.source_month_utc = required_month.source_month_utc
     AND source_row.source_grid_id = p_source_grid_id;

    IF v_expected_source_months = 0
       OR v_source_files <> v_expected_source_months * 3 THEN
        RAISE EXCEPTION
            'Incomplete HOSTRADA source manifests for local month %: % files across % required UTC months',
            p_calendar_month,
            v_source_files,
            v_expected_source_months;
    END IF;

    DELETE FROM analytical.hostrada_plr_hourly_reference AS reference_row
    WHERE reference_row.calendar_month = p_calendar_month
      AND reference_row.geography_version = p_geography_version;

    DELETE FROM analytical.hostrada_berlin_hourly_reference AS reference_row
    WHERE reference_row.calendar_month = p_calendar_month
      AND reference_row.geography_version = p_geography_version;

    WITH local_observations AS (
        SELECT
            hourly_row.plr_id,
            EXTRACT(DAY FROM local_time.valid_time_berlin)::SMALLINT
                AS calendar_day,
            EXTRACT(HOUR FROM local_time.valid_time_berlin)::SMALLINT
                AS local_hour,
            hourly_row.temperature_c,
            hourly_row.apparent_temperature_shade_c
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        JOIN analytical.hostrada_plr_hourly AS hourly_row
          ON hourly_row.source_month_utc = source_window.source_month_utc
         AND hourly_row.valid_time_utc >= source_window.start_utc
         AND hourly_row.valid_time_utc < source_window.end_utc
        CROSS JOIN LATERAL (
            SELECT hourly_row.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        ) AS local_time
        WHERE hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
          AND NOT (
              p_calendar_month = 2
              AND EXTRACT(DAY FROM local_time.valid_time_berlin) = 29
          )
    ),
    summaries AS (
        SELECT
            observation.plr_id,
            observation.calendar_day,
            observation.local_hour,
            COUNT(*)::SMALLINT AS sample_count,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.temperature_c
            ) AS temperature_percentiles,
            MAX(observation.temperature_c) AS temperature_max_c,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.apparent_temperature_shade_c
            ) AS apparent_temperature_percentiles,
            MAX(observation.apparent_temperature_shade_c)
                AS apparent_temperature_max_c
        FROM local_observations AS observation
        GROUP BY
            observation.plr_id,
            observation.calendar_day,
            observation.local_hour
    )
    INSERT INTO analytical.hostrada_plr_hourly_reference (
        calendar_month,
        geography_version,
        plr_id,
        calendar_day,
        local_hour,
        sample_count,
        temperature_median_c,
        temperature_p90_c,
        temperature_max_c,
        apparent_temperature_median_c,
        apparent_temperature_p90_c,
        apparent_temperature_max_c
    )
    SELECT
        p_calendar_month::SMALLINT,
        p_geography_version,
        summary.plr_id,
        summary.calendar_day,
        summary.local_hour,
        summary.sample_count,
        (summary.temperature_percentiles)[1],
        (summary.temperature_percentiles)[2],
        summary.temperature_max_c,
        (summary.apparent_temperature_percentiles)[1],
        (summary.apparent_temperature_percentiles)[2],
        summary.apparent_temperature_max_c
    FROM summaries AS summary;

    WITH local_observations AS (
        SELECT
            EXTRACT(DAY FROM local_time.valid_time_berlin)::SMALLINT
                AS calendar_day,
            EXTRACT(HOUR FROM local_time.valid_time_berlin)::SMALLINT
                AS local_hour,
            hourly_row.temperature_c,
            hourly_row.apparent_temperature_shade_c
        FROM analytical.hostrada_reference_source_windows(
            p_calendar_month
        ) AS source_window
        JOIN analytical.hostrada_berlin_hourly AS hourly_row
          ON hourly_row.source_month_utc = source_window.source_month_utc
         AND hourly_row.valid_time_utc >= source_window.start_utc
         AND hourly_row.valid_time_utc < source_window.end_utc
        CROSS JOIN LATERAL (
            SELECT hourly_row.valid_time_utc
                AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
        ) AS local_time
        WHERE hourly_row.geography_version = p_geography_version
          AND hourly_row.source_grid_id = p_source_grid_id
          AND NOT (
              p_calendar_month = 2
              AND EXTRACT(DAY FROM local_time.valid_time_berlin) = 29
          )
    ),
    summaries AS (
        SELECT
            observation.calendar_day,
            observation.local_hour,
            COUNT(*)::SMALLINT AS sample_count,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.temperature_c
            ) AS temperature_percentiles,
            MAX(observation.temperature_c) AS temperature_max_c,
            PERCENTILE_CONT(
                ARRAY[0.5, 0.9]::DOUBLE PRECISION[]
            ) WITHIN GROUP (
                ORDER BY observation.apparent_temperature_shade_c
            ) AS apparent_temperature_percentiles,
            MAX(observation.apparent_temperature_shade_c)
                AS apparent_temperature_max_c
        FROM local_observations AS observation
        GROUP BY
            observation.calendar_day,
            observation.local_hour
    )
    INSERT INTO analytical.hostrada_berlin_hourly_reference (
        calendar_month,
        geography_version,
        calendar_day,
        local_hour,
        sample_count,
        temperature_median_c,
        temperature_p90_c,
        temperature_max_c,
        apparent_temperature_median_c,
        apparent_temperature_p90_c,
        apparent_temperature_max_c
    )
    SELECT
        p_calendar_month::SMALLINT,
        p_geography_version,
        summary.calendar_day,
        summary.local_hour,
        summary.sample_count,
        (summary.temperature_percentiles)[1],
        (summary.temperature_percentiles)[2],
        summary.temperature_max_c,
        (summary.apparent_temperature_percentiles)[1],
        (summary.apparent_temperature_percentiles)[2],
        summary.apparent_temperature_max_c
    FROM summaries AS summary;

    SELECT *
    INTO v_quality
    FROM analytical.check_hostrada_reference_month_quality(
        p_calendar_month,
        p_geography_version,
        p_source_grid_id
    );

    IF v_quality IS NULL OR NOT v_quality.passed THEN
        RAISE EXCEPTION
            'HOSTRADA reference month % failed its quality gate: %',
            p_calendar_month,
            to_jsonb(v_quality);
    END IF;

    RETURN QUERY
    SELECT
        p_calendar_month::SMALLINT,
        v_quality.expected_plr_count,
        v_quality.expected_calendar_hour_count,
        v_quality.expected_observation_count,
        v_quality.plr_reference_count,
        v_quality.berlin_reference_count;
END;
$$;


--
-- Name: refresh_plr_weather(timestamp with time zone, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.refresh_plr_weather(p_run_time_utc timestamp with time zone, p_lead_time text) RETURNS TABLE(accepted boolean, plr_row_count bigint, expected_plr_count bigint, source_grid_id text, geography_version text, rejection_reason text)
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
        apparent_temperature_shade_c
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
            weather_row.apparent_temperature_shade_c
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


--
-- Name: refresh_plr_weather_population(timestamp with time zone, text); Type: FUNCTION; Schema: analytical; Owner: -
--

CREATE FUNCTION analytical.refresh_plr_weather_population(p_run_time_utc timestamp with time zone, p_lead_time text) RETURNS TABLE(accepted boolean, final_row_count bigint, available_population_count bigint, rejected_population_count bigint, population_reference_date date, rejection_reason text)
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
        apparent_temperature_shade_c,
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
        weather_row.apparent_temperature_shade_c,
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


--
-- Name: calculate_apparent_temperature_shade_c(double precision, double precision, double precision); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.calculate_apparent_temperature_shade_c(p_temperature_c double precision, p_relative_humidity_percent double precision, p_wind_speed_10m_ms double precision) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
    SELECT
        p_temperature_c
        + 0.33 * (
            (p_relative_humidity_percent / 100.0)
            * 6.105
            * EXP(
                17.27 * p_temperature_c
                / (237.7 + p_temperature_c)
            )
        )
        - 0.70 * p_wind_speed_10m_ms
        - 4.00;
$$;


--
-- Name: check_hostrada_plr_area_bridge_quality(text, text, integer, double precision); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_hostrada_plr_area_bridge_quality(p_geography_version text, p_source_grid_id text, p_expected_plr_count integer DEFAULT 542, p_weight_tolerance double precision DEFAULT 0.000001) RETURNS TABLE(passed boolean, bridge_row_count bigint, source_plr_count bigint, represented_plr_count bigint, source_hostrada_cell_count bigint, represented_hostrada_cell_count bigint, missing_plr_count bigint, unused_hostrada_cell_count bigint, orphan_plr_count bigint, orphan_hostrada_cell_count bigint, nonpositive_area_count bigint, invalid_fraction_count bigint, plr_weight_failure_count bigint, max_plr_weight_error double precision)
    LANGUAGE sql STABLE
    AS $$
WITH source_plrs AS (
    SELECT
        plr_row.plr_id,
        plr_row.geography_version
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version
),
source_cells AS (
    SELECT
        cell_row.source_grid_id,
        cell_row.geography_version,
        cell_row.y_index,
        cell_row.x_index
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id
),
bridge_rows AS (
    SELECT bridge_row.*
    FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id
),
bridge_summary AS (
    SELECT
        COUNT(*)::BIGINT AS bridge_row_count,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT AS represented_plr_count,
        COUNT(
            DISTINCT (bridge_row.y_index, bridge_row.x_index)
        )::BIGINT AS represented_hostrada_cell_count
    FROM bridge_rows AS bridge_row
),
source_summary AS (
    SELECT
        (SELECT COUNT(*) FROM source_plrs)::BIGINT AS source_plr_count,
        (SELECT COUNT(*) FROM source_cells)::BIGINT
            AS source_hostrada_cell_count
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
unused_cells AS (
    SELECT COUNT(*)::BIGINT AS unused_hostrada_cell_count
    FROM source_cells AS cell_row
    WHERE NOT EXISTS (
        SELECT 1
        FROM bridge_rows AS bridge_row
        WHERE bridge_row.source_grid_id = cell_row.source_grid_id
          AND bridge_row.geography_version = cell_row.geography_version
          AND bridge_row.y_index = cell_row.y_index
          AND bridge_row.x_index = cell_row.x_index
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
orphan_cells AS (
    SELECT COUNT(*)::BIGINT AS orphan_hostrada_cell_count
    FROM bridge_rows AS bridge_row
    LEFT JOIN normalized.hostrada_cell AS cell_row
      ON cell_row.source_grid_id = bridge_row.source_grid_id
     AND cell_row.geography_version = bridge_row.geography_version
     AND cell_row.y_index = bridge_row.y_index
     AND cell_row.x_index = bridge_row.x_index
    WHERE cell_row.source_grid_id IS NULL
),
bad_areas AS (
    SELECT COUNT(*)::BIGINT AS nonpositive_area_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.intersection_area_m2 <= 0
       OR bridge_row.plr_area_m2 <= 0
       OR bridge_row.hostrada_cell_area_m2 <= 0
),
bad_fractions AS (
    SELECT COUNT(*)::BIGINT AS invalid_fraction_count
    FROM bridge_rows AS bridge_row
    WHERE bridge_row.fraction_of_plr <= 0
       OR bridge_row.fraction_of_plr > 1 + p_weight_tolerance
       OR bridge_row.fraction_of_hostrada_cell <= 0
       OR bridge_row.fraction_of_hostrada_cell > 1 + p_weight_tolerance
),
plr_weight_sums AS (
    SELECT
        bridge_row.plr_id,
        SUM(bridge_row.fraction_of_plr)::DOUBLE PRECISION AS fraction_sum
    FROM bridge_rows AS bridge_row
    GROUP BY bridge_row.plr_id
),
weight_summary AS (
    SELECT
        COUNT(*) FILTER (
            WHERE ABS(weight_row.fraction_sum - 1.0) > p_weight_tolerance
        )::BIGINT AS plr_weight_failure_count,
        COALESCE(
            MAX(ABS(weight_row.fraction_sum - 1.0)),
            0.0
        )::DOUBLE PRECISION AS max_plr_weight_error
    FROM plr_weight_sums AS weight_row
)
SELECT
    (
        p_expected_plr_count > 0
        AND p_weight_tolerance >= 0
        AND source_summary.source_plr_count = p_expected_plr_count
        AND source_summary.source_hostrada_cell_count > 0
        AND bridge_summary.bridge_row_count > 0
        AND bridge_summary.represented_plr_count
            = source_summary.source_plr_count
        AND bridge_summary.represented_hostrada_cell_count
            = source_summary.source_hostrada_cell_count
        AND missing_plrs.missing_plr_count = 0
        AND unused_cells.unused_hostrada_cell_count = 0
        AND orphan_plrs.orphan_plr_count = 0
        AND orphan_cells.orphan_hostrada_cell_count = 0
        AND bad_areas.nonpositive_area_count = 0
        AND bad_fractions.invalid_fraction_count = 0
        AND weight_summary.plr_weight_failure_count = 0
    ) AS passed,
    bridge_summary.bridge_row_count,
    source_summary.source_plr_count,
    bridge_summary.represented_plr_count,
    source_summary.source_hostrada_cell_count,
    bridge_summary.represented_hostrada_cell_count,
    missing_plrs.missing_plr_count,
    unused_cells.unused_hostrada_cell_count,
    orphan_plrs.orphan_plr_count,
    orphan_cells.orphan_hostrada_cell_count,
    bad_areas.nonpositive_area_count,
    bad_fractions.invalid_fraction_count,
    weight_summary.plr_weight_failure_count,
    weight_summary.max_plr_weight_error
FROM bridge_summary
CROSS JOIN source_summary
CROSS JOIN missing_plrs
CROSS JOIN unused_cells
CROSS JOIN orphan_plrs
CROSS JOIN orphan_cells
CROSS JOIN bad_areas
CROSS JOIN bad_fractions
CROSS JOIN weight_summary;
$$;


--
-- Name: check_icon_d2_ruc_weather_quality(timestamp with time zone, text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_icon_d2_ruc_weather_quality(p_run_time_utc timestamp with time zone, p_lead_time text) RETURNS TABLE(passed boolean, normalized_row_count bigint, expected_mask_cell_count bigint, bridge_cell_count bigint, missing_mask_cell_count bigint, outside_mask_cell_count bigint, bridge_missing_value_count bigint, conversion_mismatch_count bigint, rejected_partition_count bigint)
    LANGUAGE sql STABLE
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
        weather_row.temperature_c,
        weather_row.apparent_temperature_shade_c
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
               OR weather_row.apparent_temperature_shade_c IS NULL
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


--
-- Name: check_icon_geometry_quality(text, integer, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_icon_geometry_quality(p_source_grid_id text, p_expected_vertex_count integer DEFAULT 272089, p_expected_cell_count integer DEFAULT 542040) RETURNS TABLE(passed boolean, raw_vertex_count bigint, raw_cell_count bigint, topology_row_count bigint, normalized_cell_count bigint, rejected_cell_count bigint, invalid_normalized_geometry_count bigint, wrong_srid_count bigint, non_triangle_count bigint, rejection_reasons jsonb)
    LANGUAGE sql STABLE
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


--
-- Name: check_icon_plr_area_bridge_quality(text, text, integer, double precision); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_icon_plr_area_bridge_quality(p_geography_version text, p_source_grid_id text, p_expected_plr_count integer DEFAULT 542, p_weight_tolerance double precision DEFAULT 0.000001) RETURNS TABLE(passed boolean, bridge_row_count bigint, source_plr_count bigint, represented_plr_count bigint, represented_icon_cell_count bigint, missing_plr_count bigint, orphan_plr_count bigint, orphan_icon_cell_count bigint, nonpositive_area_count bigint, invalid_fraction_count bigint, plr_weight_failure_count bigint, max_plr_weight_error double precision)
    LANGUAGE sql STABLE
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


--
-- Name: check_icon_weather_mask_quality(text, text, integer, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_icon_weather_mask_quality(p_geography_version text, p_source_grid_id text, p_mask_buffer_m integer DEFAULT 5000, p_expected_plr_count integer DEFAULT 542) RETURNS TABLE(passed boolean, source_plr_count bigint, mask_cell_count bigint, bridge_cell_count bigint, missing_bridge_cell_count bigint, orphan_mask_cell_count bigint)
    LANGUAGE sql STABLE
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


--
-- Name: check_plr_geometry_quality(text, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_plr_geometry_quality(p_source_sha256 text, p_expected_plr_count integer DEFAULT 542) RETURNS TABLE(passed boolean, source_row_count bigint, normalized_row_count bigint, rejected_row_count bigint, invalid_normalized_geometry_count bigint, wrong_srid_count bigint, geography_version text, rejection_reasons jsonb)
    LANGUAGE sql STABLE
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


--
-- Name: check_population_quality(text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.check_population_quality(p_source_sha256 text) RETURNS TABLE(passed boolean, source_row_count bigint, accepted_row_count bigint, rejected_row_count bigint, accepted_rejected_overlap bigint, rejection_reasons jsonb)
    LANGUAGE sql STABLE
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


--
-- Name: classify_afs_population(text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.classify_afs_population(p_source_sha256 text) RETURNS TABLE(plr_id text, population_total bigint, population_65_79 bigint, population_80plus bigint, population_65plus bigint, share_65plus double precision, rejection_reason text, reference_date date, publication_date date, source_sha256 text)
    LANGUAGE sql
    AS $_$
WITH source_rows AS (
    SELECT
        NULLIF(BTRIM(plr_id_source), '') AS plr_id,
        population_total_source,
        population_65_79_source,
        population_80plus_source,
        reference_code_source,
        publication_date,
        source_sha256
    FROM raw.afs_population
    WHERE source_sha256 = p_source_sha256
),
parsed AS (
    SELECT
        plr_id,
        population_total_source,
        population_65_79_source,
        population_80plus_source,

        CASE
            WHEN NULLIF(BTRIM(population_total_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_total_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_total_source)::BIGINT
            ELSE NULL
        END AS population_total,

        CASE
            WHEN NULLIF(BTRIM(population_65_79_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_65_79_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_65_79_source)::BIGINT
            ELSE NULL
        END AS population_65_79,

        CASE
            WHEN NULLIF(BTRIM(population_80plus_source), '') IS NULL
                THEN NULL
            WHEN BTRIM(population_80plus_source) ~ '^-?[0-9]+$'
                THEN BTRIM(population_80plus_source)::BIGINT
            ELSE NULL
        END AS population_80plus,

        NULLIF(BTRIM(population_total_source), '') IS NOT NULL
            AND BTRIM(population_total_source) !~ '^-?[0-9]+$'
            AS invalid_population_total_source,

        NULLIF(BTRIM(population_65_79_source), '') IS NOT NULL
            AND BTRIM(population_65_79_source) !~ '^-?[0-9]+$'
            AS invalid_population_65_79_source,

        NULLIF(BTRIM(population_80plus_source), '') IS NOT NULL
            AND BTRIM(population_80plus_source) !~ '^-?[0-9]+$'
            AS invalid_population_80plus_source,

        (
            TO_DATE(reference_code_source || '01', 'YYYYMMDD')
            + INTERVAL '1 month'
            - INTERVAL '1 day'
        )::DATE AS reference_date,

        publication_date,
        source_sha256
    FROM source_rows
),
derived AS (
    SELECT
        *,
        CASE
            WHEN population_65_79 IS NULL
              OR population_80plus IS NULL
                THEN NULL
            ELSE population_65_79 + population_80plus
        END AS population_65plus
    FROM parsed
),
classified AS (
    SELECT
        *,
        CASE
            WHEN population_total IS NULL
             AND NOT invalid_population_total_source
                THEN 'missing_population_total'

            WHEN invalid_population_total_source
                THEN 'invalid_population_total'

            WHEN (
                population_65_79 IS NULL
                AND NOT invalid_population_65_79_source
            )
              OR (
                population_80plus IS NULL
                AND NOT invalid_population_80plus_source
            )
                THEN 'missing_population_65plus_component'

            WHEN invalid_population_65_79_source
              OR invalid_population_80plus_source
                THEN 'invalid_population_65plus_component'

            WHEN population_total < 0
                THEN 'negative_population_total'

            WHEN population_total = 0
                THEN 'zero_population_total'

            WHEN population_65_79 < 0
              OR population_80plus < 0
              OR population_65plus < 0
              OR population_65_79 > population_total
              OR population_80plus > population_total
              OR population_65plus > population_total
                THEN 'invalid_population_65plus'

            ELSE NULL
        END AS rejection_reason
    FROM derived
)
SELECT
    plr_id,
    population_total,
    population_65_79,
    population_80plus,
    population_65plus,
    CASE
        WHEN rejection_reason IS NULL
            THEN population_65plus::DOUBLE PRECISION
                 / population_total::DOUBLE PRECISION
        ELSE NULL
    END AS share_65plus,
    rejection_reason,
    reference_date,
    publication_date,
    source_sha256
FROM classified
$_$;


--
-- Name: refresh_hostrada_cell_geometry(text, text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_hostrada_cell_geometry(p_geography_version text, p_source_grid_id text) RETURNS TABLE(cell_row_count bigint, represented_plr_count bigint, candidate_cell_count bigint)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_grid normalized.hostrada_grid%ROWTYPE;
    v_extent BOX3D;
    v_source_plr_count BIGINT;
    v_x_start INTEGER;
    v_x_stop INTEGER;
    v_y_start INTEGER;
    v_y_stop INTEGER;
BEGIN
    IF p_geography_version IS NULL
       OR BTRIM(p_geography_version) = '' THEN
        RAISE EXCEPTION 'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR BTRIM(p_source_grid_id) = '' THEN
        RAISE EXCEPTION 'p_source_grid_id must be non-empty';
    END IF;

    SELECT grid_row.*
    INTO v_grid
    FROM normalized.hostrada_grid AS grid_row
    WHERE grid_row.source_grid_id = p_source_grid_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'HOSTRADA grid % has not been registered',
            p_source_grid_id;
    END IF;

    SELECT
        COUNT(*)::BIGINT,
        ST_Extent(
            ST_Transform(plr_row.geometry, v_grid.source_srid)
        )::BOX3D
    INTO
        v_source_plr_count,
        v_extent
    FROM normalized.plr AS plr_row
    WHERE plr_row.geography_version = p_geography_version;

    IF v_source_plr_count = 0 OR v_extent IS NULL THEN
        RAISE EXCEPTION
            'No normalized PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF ST_XMin(v_extent) < v_grid.x_origin_m - v_grid.x_spacing_m / 2.0
       OR ST_YMin(v_extent) < v_grid.y_origin_m - v_grid.y_spacing_m / 2.0
       OR ST_XMax(v_extent)
            > v_grid.x_origin_m
              + (v_grid.x_count - 1) * v_grid.x_spacing_m
              + v_grid.x_spacing_m / 2.0
       OR ST_YMax(v_extent)
            > v_grid.y_origin_m
              + (v_grid.y_count - 1) * v_grid.y_spacing_m
              + v_grid.y_spacing_m / 2.0 THEN
        RAISE EXCEPTION
            'PLR geography % is not fully covered by HOSTRADA grid %',
            p_geography_version,
            p_source_grid_id;
    END IF;

    v_x_start := GREATEST(
        0,
        CEIL(
            (
                ST_XMin(v_extent)
                - v_grid.x_origin_m
                - v_grid.x_spacing_m / 2.0
            ) / v_grid.x_spacing_m
        )::INTEGER
    );
    v_x_stop := LEAST(
        v_grid.x_count,
        FLOOR(
            (
                ST_XMax(v_extent)
                - v_grid.x_origin_m
                + v_grid.x_spacing_m / 2.0
            ) / v_grid.x_spacing_m
        )::INTEGER + 1
    );
    v_y_start := GREATEST(
        0,
        CEIL(
            (
                ST_YMin(v_extent)
                - v_grid.y_origin_m
                - v_grid.y_spacing_m / 2.0
            ) / v_grid.y_spacing_m
        )::INTEGER
    );
    v_y_stop := LEAST(
        v_grid.y_count,
        FLOOR(
            (
                ST_YMax(v_extent)
                - v_grid.y_origin_m
                + v_grid.y_spacing_m / 2.0
            ) / v_grid.y_spacing_m
        )::INTEGER + 1
    );

    IF v_x_start >= v_x_stop OR v_y_start >= v_y_stop THEN
        RAISE EXCEPTION
            'PLR geography % does not overlap HOSTRADA grid %',
            p_geography_version,
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.hostrada_cell (
        source_grid_id,
        geography_version,
        y_index,
        x_index,
        geometry,
        hostrada_cell_area_m2
    )
    WITH candidate_cells AS MATERIALIZED (
        SELECT
            y_grid.y_index,
            x_grid.x_index,
            ST_Transform(
                ST_MakeEnvelope(
                    v_grid.x_origin_m
                        + x_grid.x_index * v_grid.x_spacing_m
                        - v_grid.x_spacing_m / 2.0,
                    v_grid.y_origin_m
                        + y_grid.y_index * v_grid.y_spacing_m
                        - v_grid.y_spacing_m / 2.0,
                    v_grid.x_origin_m
                        + x_grid.x_index * v_grid.x_spacing_m
                        + v_grid.x_spacing_m / 2.0,
                    v_grid.y_origin_m
                        + y_grid.y_index * v_grid.y_spacing_m
                        + v_grid.y_spacing_m / 2.0,
                    v_grid.source_srid
                ),
                v_grid.target_srid
            )::geometry(Polygon, 25833) AS geometry
        FROM generate_series(v_x_start, v_x_stop - 1) AS x_grid(x_index)
        CROSS JOIN generate_series(v_y_start, v_y_stop - 1) AS y_grid(y_index)
    )
    SELECT
        p_source_grid_id,
        p_geography_version,
        candidate.y_index,
        candidate.x_index,
        candidate.geometry,
        ST_Area(candidate.geometry)::DOUBLE PRECISION
    FROM candidate_cells AS candidate
    WHERE EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
          AND plr_row.geometry && candidate.geometry
          AND ST_Intersects(plr_row.geometry, candidate.geometry)
          AND ST_Area(
                ST_Intersection(plr_row.geometry, candidate.geometry)
              ) > 0
    );

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        (
            SELECT COUNT(DISTINCT plr_row.plr_id)::BIGINT
            FROM normalized.plr AS plr_row
            JOIN normalized.hostrada_cell AS cell_row
              ON cell_row.source_grid_id = p_source_grid_id
             AND cell_row.geography_version = p_geography_version
             AND plr_row.geometry && cell_row.geometry
             AND ST_Intersects(plr_row.geometry, cell_row.geometry)
             AND ST_Area(
                    ST_Intersection(plr_row.geometry, cell_row.geometry)
                 ) > 0
            WHERE plr_row.geography_version = p_geography_version
        ),
        ((v_x_stop - v_x_start) * (v_y_stop - v_y_start))::BIGINT
    FROM normalized.hostrada_cell AS cell_row
    WHERE cell_row.geography_version = p_geography_version
      AND cell_row.source_grid_id = p_source_grid_id;
END;
$$;


--
-- Name: refresh_hostrada_plr_area_bridge(text, text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_hostrada_plr_area_bridge(p_geography_version text, p_source_grid_id text) RETURNS TABLE(bridge_row_count bigint, represented_plr_count bigint, represented_hostrada_cell_count bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_geography_version IS NULL
       OR BTRIM(p_geography_version) = '' THEN
        RAISE EXCEPTION 'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR BTRIM(p_source_grid_id) = '' THEN
        RAISE EXCEPTION 'p_source_grid_id must be non-empty';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No normalized PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.hostrada_cell AS cell_row
        WHERE cell_row.source_grid_id = p_source_grid_id
          AND cell_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No HOSTRADA cells found for geography_version=% and grid=%',
            p_geography_version,
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.hostrada_plr_area_bridge (
        plr_id,
        geography_version,
        source_grid_id,
        y_index,
        x_index,
        intersection_area_m2,
        plr_area_m2,
        hostrada_cell_area_m2,
        fraction_of_plr,
        fraction_of_hostrada_cell
    )
    WITH measured_intersections AS (
        SELECT
            plr_row.plr_id,
            plr_row.geography_version,
            cell_row.source_grid_id,
            cell_row.y_index,
            cell_row.x_index,
            ST_Area(plr_row.geometry)::DOUBLE PRECISION AS plr_area_m2,
            cell_row.hostrada_cell_area_m2,
            ST_Area(
                ST_Intersection(plr_row.geometry, cell_row.geometry)
            )::DOUBLE PRECISION AS intersection_area_m2
        FROM normalized.plr AS plr_row
        JOIN normalized.hostrada_cell AS cell_row
          ON cell_row.source_grid_id = p_source_grid_id
         AND cell_row.geography_version = p_geography_version
         AND plr_row.geometry && cell_row.geometry
         AND ST_Intersects(plr_row.geometry, cell_row.geometry)
        WHERE plr_row.geography_version = p_geography_version
    )
    SELECT
        measured.plr_id,
        measured.geography_version,
        measured.source_grid_id,
        measured.y_index,
        measured.x_index,
        measured.intersection_area_m2,
        measured.plr_area_m2,
        measured.hostrada_cell_area_m2,
        measured.intersection_area_m2 / measured.plr_area_m2,
        measured.intersection_area_m2 / measured.hostrada_cell_area_m2
    FROM measured_intersections AS measured
    WHERE measured.intersection_area_m2 > 0
      AND measured.plr_area_m2 > 0
      AND measured.hostrada_cell_area_m2 > 0;

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT,
        COUNT(
            DISTINCT (bridge_row.y_index, bridge_row.x_index)
        )::BIGINT
    FROM normalized.hostrada_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;
END;
$$;


--
-- Name: refresh_icon_cell_geometry(text, integer, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_icon_cell_geometry(p_source_grid_id text, p_expected_vertex_count integer DEFAULT 272089, p_expected_cell_count integer DEFAULT 542040) RETURNS TABLE(raw_vertex_count bigint, raw_cell_count bigint, normalized_cell_count bigint, rejected_cell_count bigint, rejection_reasons jsonb)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_vertex_count BIGINT;
    v_cell_count BIGINT;
    v_normalized_count BIGINT;
    v_rejected_count BIGINT;
    v_reasons JSONB;
BEGIN
    SELECT COUNT(*)
    INTO v_vertex_count
    FROM raw.icon_grid_vertex AS vertex
    WHERE vertex.source_grid_id = p_source_grid_id;

    SELECT COUNT(DISTINCT topology.cell_index)
    INTO v_cell_count
    FROM raw.icon_grid_cell_vertex AS topology
    WHERE topology.source_grid_id = p_source_grid_id;

    IF v_vertex_count = 0 OR v_cell_count = 0 THEN
        RAISE EXCEPTION
            'ICON grid % has no raw vertex/topology data',
            p_source_grid_id;
    END IF;

    DROP TABLE IF EXISTS pg_temp.icon_cell_candidates;

    CREATE TEMP TABLE icon_cell_candidates
    ON COMMIT DROP
    AS
    WITH grouped AS (
        SELECT
            topology.source_grid_id,
            topology.cell_index,
            COUNT(*) AS vertex_count,
            COUNT(DISTINCT topology.vertex_index)
                AS distinct_vertex_count,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 0) AS lon_0,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 0) AS lat_0,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 1) AS lon_1,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 1) AS lat_1,

            MAX(vertex.longitude_deg)
                FILTER (WHERE topology.vertex_order = 2) AS lon_2,
            MAX(vertex.latitude_deg)
                FILTER (WHERE topology.vertex_order = 2) AS lat_2
        FROM raw.icon_grid_cell_vertex AS topology
        JOIN raw.icon_grid_vertex AS vertex
          ON vertex.source_grid_id = topology.source_grid_id
         AND vertex.vertex_index = topology.vertex_index
        WHERE topology.source_grid_id = p_source_grid_id
        GROUP BY
            topology.source_grid_id,
            topology.cell_index
    ),
    preliminary AS (
        SELECT
            grouped.*,
            CASE
                WHEN grouped.vertex_count <> 3
                    THEN 'invalid_vertex_count'
                WHEN grouped.distinct_vertex_count <> 3
                    THEN 'repeated_vertex'
                WHEN grouped.lon_0 IS NULL
                  OR grouped.lat_0 IS NULL
                  OR grouped.lon_1 IS NULL
                  OR grouped.lat_1 IS NULL
                  OR grouped.lon_2 IS NULL
                  OR grouped.lat_2 IS NULL
                    THEN 'missing_vertex_coordinate'
                WHEN grouped.lon_0 NOT BETWEEN -180 AND 180
                  OR grouped.lon_1 NOT BETWEEN -180 AND 180
                  OR grouped.lon_2 NOT BETWEEN -180 AND 180
                  OR grouped.lat_0 NOT BETWEEN -90 AND 90
                  OR grouped.lat_1 NOT BETWEEN -90 AND 90
                  OR grouped.lat_2 NOT BETWEEN -90 AND 90
                    THEN 'invalid_vertex_coordinate'
                ELSE NULL
            END AS preliminary_rejection_reason
        FROM grouped
    ),
    geometry_built AS (
        SELECT
            preliminary.source_grid_id,
            preliminary.cell_index,
            CASE
                WHEN preliminary.preliminary_rejection_reason IS NULL
                THEN ST_Transform(
                    ST_SetSRID(
                        ST_MakePolygon(
                            ST_MakeLine(
                                ARRAY[
                                    ST_MakePoint(
                                        preliminary.lon_0,
                                        preliminary.lat_0
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_1,
                                        preliminary.lat_1
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_2,
                                        preliminary.lat_2
                                    ),
                                    ST_MakePoint(
                                        preliminary.lon_0,
                                        preliminary.lat_0
                                    )
                                ]
                            )
                        ),
                        4326
                    ),
                    25833
                )::geometry(Polygon, 25833)
                ELSE NULL
            END AS geometry,
            preliminary.preliminary_rejection_reason
        FROM preliminary
    )
    SELECT
        geometry_built.source_grid_id,
        geometry_built.cell_index,
        geometry_built.geometry,
        CASE
            WHEN geometry_built.preliminary_rejection_reason IS NOT NULL
                THEN geometry_built.preliminary_rejection_reason
            WHEN geometry_built.geometry IS NULL
                THEN 'geometry_construction_failed'
            WHEN ST_IsEmpty(geometry_built.geometry)
                THEN 'empty_geometry'
            WHEN NOT ST_IsValid(geometry_built.geometry)
                THEN 'invalid_geometry'
            WHEN ST_Area(geometry_built.geometry) <= 0
                THEN 'non_positive_area'
            WHEN ST_NPoints(
                ST_ExteriorRing(geometry_built.geometry)
            ) <> 4
                THEN 'not_triangular'
            ELSE NULL
        END AS rejection_reason
    FROM geometry_built;

    DELETE FROM normalized.icon_geometry_rejected AS rejected
    WHERE rejected.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.icon_geometry_rejected (
        source_grid_id,
        cell_index,
        rejection_reason
    )
    SELECT
        candidate.source_grid_id,
        candidate.cell_index,
        candidate.rejection_reason
    FROM icon_cell_candidates AS candidate
    WHERE candidate.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    INSERT INTO normalized.icon_cell (
        source_grid_id,
        cell_index,
        geometry,
        icon_cell_area_m2
    )
    SELECT
        candidate.source_grid_id,
        candidate.cell_index,
        candidate.geometry,
        ST_Area(candidate.geometry)
    FROM icon_cell_candidates AS candidate
    WHERE candidate.rejection_reason IS NULL
    ON CONFLICT (
        source_grid_id,
        cell_index
    )
    DO UPDATE SET
        geometry = EXCLUDED.geometry,
        icon_cell_area_m2 = EXCLUDED.icon_cell_area_m2;

    SELECT COUNT(*)
    INTO v_normalized_count
    FROM normalized.icon_cell AS normalized_cell
    WHERE normalized_cell.source_grid_id = p_source_grid_id;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.icon_geometry_rejected AS rejected
        WHERE rejected.source_grid_id = p_source_grid_id
        GROUP BY rejected.rejection_reason
    ) AS reason_counts;

    RETURN QUERY
    SELECT
        v_vertex_count,
        v_cell_count,
        v_normalized_count,
        v_rejected_count,
        v_reasons;
END
$$;


--
-- Name: refresh_icon_d2_ruc_weather(timestamp with time zone, text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_icon_d2_ruc_weather(p_run_time_utc timestamp with time zone, p_lead_time text) RETURNS TABLE(accepted boolean, normalized_row_count bigint, expected_mask_cell_count bigint, bridge_cell_count bigint, bridge_missing_value_count bigint, invalid_unit_indicator_count bigint, rejection_reason text)
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

    WITH cell_inputs AS (
        SELECT
            field_row.cell_index,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'T_2M'
            ) AS temperature_k,
            MAX(field_row.source_value) FILTER (
                WHERE field_row.indicator = 'RELHUM_2M'
            ) AS relative_humidity_percent,
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
    ),
    physical_inputs AS (
        SELECT
            cell_inputs.cell_index,
            cell_inputs.temperature_k - 273.15
                AS temperature_c,
            cell_inputs.relative_humidity_percent,
            SQRT(
                POWER(cell_inputs.wind_u_10m_ms, 2)
                + POWER(cell_inputs.wind_v_10m_ms, 2)
            ) AS wind_speed_10m_ms
        FROM cell_inputs
    )
    INSERT INTO normalized.icon_d2_ruc_weather (
        run_time_utc,
        lead_time,
        valid_time_utc,
        source_grid_id,
        geography_version,
        mask_buffer_m,
        cell_index,
        temperature_c,
        apparent_temperature_shade_c
    )
    SELECT
        p_run_time_utc,
        p_lead_time,
        v_valid_time_utc,
        v_source_grid_id,
        v_geography_version,
        v_mask_buffer_m,
        physical_inputs.cell_index,
        physical_inputs.temperature_c,
        normalized.calculate_apparent_temperature_shade_c(
            physical_inputs.temperature_c,
            physical_inputs.relative_humidity_percent,
            physical_inputs.wind_speed_10m_ms
        )
    FROM physical_inputs;

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
       OR weather_row.apparent_temperature_shade_c IS NULL;

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


--
-- Name: refresh_icon_plr_area_bridge(text, text); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_icon_plr_area_bridge(p_geography_version text, p_source_grid_id text) RETURNS TABLE(bridge_row_count bigint, represented_plr_count bigint, represented_icon_cell_count bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_geography_version IS NULL
       OR btrim(p_geography_version) = '' THEN
        RAISE EXCEPTION
            'p_geography_version must be non-empty';
    END IF;

    IF p_source_grid_id IS NULL
       OR btrim(p_source_grid_id) = '' THEN
        RAISE EXCEPTION
            'p_source_grid_id must be non-empty';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No normalized PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.icon_cell AS icon_row
        WHERE icon_row.source_grid_id = p_source_grid_id
    ) THEN
        RAISE EXCEPTION
            'No normalized ICON cells found for source_grid_id=%',
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;

    INSERT INTO normalized.icon_plr_area_bridge (
        plr_id,
        geography_version,
        source_grid_id,
        cell_index,
        intersection_area_m2,
        plr_area_m2,
        icon_cell_area_m2,
        fraction_of_plr,
        fraction_of_icon_cell
    )
    WITH candidate_pairs AS (
        SELECT
            plr_row.plr_id,
            plr_row.geography_version,
            icon_row.source_grid_id,
            icon_row.cell_index,
            plr_row.geometry AS plr_geometry,
            icon_row.geometry AS icon_geometry,
            ST_Area(plr_row.geometry)::DOUBLE PRECISION
                AS plr_area_m2,
            ST_Area(icon_row.geometry)::DOUBLE PRECISION
                AS icon_cell_area_m2
        FROM normalized.plr AS plr_row
        JOIN normalized.icon_cell AS icon_row
          ON icon_row.source_grid_id = p_source_grid_id
         AND plr_row.geometry && icon_row.geometry
         AND ST_Intersects(
                plr_row.geometry,
                icon_row.geometry
            )
        WHERE plr_row.geography_version = p_geography_version
    ),
    measured_intersections AS (
        SELECT
            candidate.plr_id,
            candidate.geography_version,
            candidate.source_grid_id,
            candidate.cell_index,
            candidate.plr_area_m2,
            candidate.icon_cell_area_m2,
            ST_Area(
                ST_Intersection(
                    candidate.plr_geometry,
                    candidate.icon_geometry
                )
            )::DOUBLE PRECISION AS intersection_area_m2
        FROM candidate_pairs AS candidate
    )
    SELECT
        measured.plr_id,
        measured.geography_version,
        measured.source_grid_id,
        measured.cell_index,
        measured.intersection_area_m2,
        measured.plr_area_m2,
        measured.icon_cell_area_m2,
        measured.intersection_area_m2 / measured.plr_area_m2,
        measured.intersection_area_m2 / measured.icon_cell_area_m2
    FROM measured_intersections AS measured
    WHERE measured.intersection_area_m2 > 0
      AND measured.plr_area_m2 > 0
      AND measured.icon_cell_area_m2 > 0;

    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        COUNT(DISTINCT bridge_row.plr_id)::BIGINT,
        COUNT(DISTINCT bridge_row.cell_index)::BIGINT
    FROM normalized.icon_plr_area_bridge AS bridge_row
    WHERE bridge_row.geography_version = p_geography_version
      AND bridge_row.source_grid_id = p_source_grid_id;
END;
$$;


--
-- Name: refresh_icon_weather_mask(text, text, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_icon_weather_mask(p_geography_version text, p_source_grid_id text, p_mask_buffer_m integer DEFAULT 5000) RETURNS TABLE(mask_cell_count bigint, bridge_cell_count bigint, missing_bridge_cell_count bigint)
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF p_mask_buffer_m < 0 THEN
        RAISE EXCEPTION 'mask buffer must be non-negative';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.icon_cell AS icon_row
        WHERE icon_row.source_grid_id = p_source_grid_id
    ) THEN
        RAISE EXCEPTION
            'No ICON cells found for source_grid_id=%',
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.icon_weather_mask AS mask_row
    WHERE mask_row.geography_version = p_geography_version
      AND mask_row.source_grid_id = p_source_grid_id
      AND mask_row.mask_buffer_m = p_mask_buffer_m;

    INSERT INTO normalized.icon_weather_mask (
        geography_version,
        source_grid_id,
        mask_buffer_m,
        cell_index
    )
    WITH berlin AS (
        SELECT
            ST_Buffer(
                ST_UnaryUnion(ST_Collect(plr_row.geometry)),
                p_mask_buffer_m
            ) AS geometry
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    )
    SELECT
        p_geography_version,
        icon_row.source_grid_id,
        p_mask_buffer_m,
        icon_row.cell_index
    FROM normalized.icon_cell AS icon_row
    CROSS JOIN berlin
    WHERE icon_row.source_grid_id = p_source_grid_id
      AND icon_row.geometry && berlin.geometry
      AND ST_Intersects(icon_row.geometry, berlin.geometry);

    RETURN QUERY
    WITH bridge_cells AS (
        SELECT DISTINCT bridge_row.cell_index
        FROM normalized.icon_plr_area_bridge AS bridge_row
        WHERE bridge_row.geography_version = p_geography_version
          AND bridge_row.source_grid_id = p_source_grid_id
    ),
    mask_cells AS (
        SELECT mask_row.cell_index
        FROM normalized.icon_weather_mask AS mask_row
        WHERE mask_row.geography_version = p_geography_version
          AND mask_row.source_grid_id = p_source_grid_id
          AND mask_row.mask_buffer_m = p_mask_buffer_m
    )
    SELECT
        (SELECT COUNT(*) FROM mask_cells)::BIGINT,
        (SELECT COUNT(*) FROM bridge_cells)::BIGINT,
        (
            SELECT COUNT(*)
            FROM bridge_cells AS bridge_cell
            WHERE NOT EXISTS (
                SELECT 1
                FROM mask_cells AS mask_cell
                WHERE mask_cell.cell_index = bridge_cell.cell_index
            )
        )::BIGINT;
END;
$$;


--
-- Name: refresh_plr_geometry(text, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_plr_geometry(p_source_sha256 text, p_expected_plr_count integer DEFAULT 542) RETURNS TABLE(source_row_count bigint, normalized_row_count bigint, rejected_row_count bigint, geography_version text, rejection_reasons jsonb)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_source_count BIGINT;
    v_normalized_count BIGINT;
    v_rejected_count BIGINT;
    v_geography_version_count BIGINT;
    v_geography_version TEXT;
    v_reasons JSONB;
BEGIN
    SELECT
        COUNT(*),
        COUNT(DISTINCT raw_lor.geography_version),
        MIN(raw_lor.geography_version)
    INTO
        v_source_count,
        v_geography_version_count,
        v_geography_version
    FROM raw.lor_plr AS raw_lor
    WHERE raw_lor.source_sha256 = p_source_sha256;

    IF v_source_count = 0 THEN
        RAISE EXCEPTION
            'No raw LOR rows found for source_sha256 %',
            p_source_sha256;
    END IF;

    IF v_geography_version_count <> 1 THEN
        RAISE EXCEPTION
            'LOR source must contain exactly one geography version; got %',
            v_geography_version_count;
    END IF;

    DELETE FROM normalized.plr_geometry_rejected AS rejected
    WHERE rejected.source_sha256 = p_source_sha256;

    WITH classified AS (
        SELECT
            raw_lor.source_row_id,
            NULLIF(BTRIM(raw_lor.plr_id_source), '') AS plr_id,
            raw_lor.geography_version,
            raw_lor.reference_date,
            raw_lor.source_sha256,
            raw_lor.geometry_source,
            COUNT(*) OVER (
                PARTITION BY
                    NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                    raw_lor.geography_version
            ) AS id_version_count,
            CASE
                WHEN NULLIF(BTRIM(raw_lor.plr_id_source), '') IS NULL
                    THEN 'missing_plr_id'
                WHEN raw_lor.geography_version IS NULL
                    THEN 'missing_geography_version'
                WHEN COUNT(*) OVER (
                    PARTITION BY
                        NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                        raw_lor.geography_version
                ) > 1
                    THEN 'duplicate_plr_id'
                WHEN raw_lor.geometry_source IS NULL
                    THEN 'missing_geometry'
                WHEN ST_IsEmpty(raw_lor.geometry_source)
                    THEN 'empty_geometry'
                WHEN ST_SRID(raw_lor.geometry_source) = 0
                    THEN 'missing_srid'
                WHEN GeometryType(raw_lor.geometry_source)
                     NOT IN ('POLYGON', 'MULTIPOLYGON')
                    THEN 'non_polygonal_geometry'
                WHEN NOT ST_IsValid(raw_lor.geometry_source)
                    THEN 'invalid_geometry'
                ELSE NULL
            END AS rejection_reason
        FROM raw.lor_plr AS raw_lor
        WHERE raw_lor.source_sha256 = p_source_sha256
    )
    INSERT INTO normalized.plr_geometry_rejected (
        source_sha256,
        source_row_id,
        plr_id,
        geography_version,
        rejection_reason
    )
    SELECT
        classified.source_sha256,
        classified.source_row_id,
        classified.plr_id,
        classified.geography_version,
        classified.rejection_reason
    FROM classified
    WHERE classified.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    WITH classified AS (
        SELECT
            raw_lor.source_row_id,
            NULLIF(BTRIM(raw_lor.plr_id_source), '') AS plr_id,
            raw_lor.geography_version,
            raw_lor.reference_date,
            raw_lor.source_sha256,
            raw_lor.geometry_source,
            COUNT(*) OVER (
                PARTITION BY
                    NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                    raw_lor.geography_version
            ) AS id_version_count,
            CASE
                WHEN NULLIF(BTRIM(raw_lor.plr_id_source), '') IS NULL
                    THEN 'missing_plr_id'
                WHEN raw_lor.geography_version IS NULL
                    THEN 'missing_geography_version'
                WHEN COUNT(*) OVER (
                    PARTITION BY
                        NULLIF(BTRIM(raw_lor.plr_id_source), ''),
                        raw_lor.geography_version
                ) > 1
                    THEN 'duplicate_plr_id'
                WHEN raw_lor.geometry_source IS NULL
                    THEN 'missing_geometry'
                WHEN ST_IsEmpty(raw_lor.geometry_source)
                    THEN 'empty_geometry'
                WHEN ST_SRID(raw_lor.geometry_source) = 0
                    THEN 'missing_srid'
                WHEN GeometryType(raw_lor.geometry_source)
                     NOT IN ('POLYGON', 'MULTIPOLYGON')
                    THEN 'non_polygonal_geometry'
                WHEN NOT ST_IsValid(raw_lor.geometry_source)
                    THEN 'invalid_geometry'
                ELSE NULL
            END AS rejection_reason
        FROM raw.lor_plr AS raw_lor
        WHERE raw_lor.source_sha256 = p_source_sha256
    ),
    canonical AS (
        SELECT
            classified.plr_id,
            classified.geography_version,
            classified.reference_date,
            classified.source_sha256,
            ST_Multi(
                CASE
                    WHEN ST_SRID(classified.geometry_source) = 25833
                        THEN classified.geometry_source
                    ELSE ST_Transform(
                        classified.geometry_source,
                        25833
                    )
                END
            )::geometry(MultiPolygon, 25833) AS geometry
        FROM classified
        WHERE classified.rejection_reason IS NULL
    )
    INSERT INTO normalized.plr (
        plr_id,
        geometry,
        geography_version,
        reference_date,
        source_sha256
    )
    SELECT
        canonical.plr_id,
        canonical.geometry,
        canonical.geography_version,
        canonical.reference_date,
        canonical.source_sha256
    FROM canonical
    WHERE ST_IsValid(canonical.geometry)
      AND NOT ST_IsEmpty(canonical.geometry)
      AND ST_Area(canonical.geometry) > 0
    ON CONFLICT ON CONSTRAINT plr_pkey
    DO UPDATE SET
        geometry = EXCLUDED.geometry,
        reference_date = EXCLUDED.reference_date,
        source_sha256 = EXCLUDED.source_sha256;

    SELECT COUNT(*)
    INTO v_normalized_count
    FROM normalized.plr AS normalized_plr
    WHERE normalized_plr.source_sha256 = p_source_sha256;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(
            reason_counts.rejection_reason,
            reason_counts.reason_count
        ),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.plr_geometry_rejected AS rejected
        WHERE rejected.source_sha256 = p_source_sha256
        GROUP BY rejected.rejection_reason
    ) AS reason_counts;

    RETURN QUERY
    SELECT
        v_source_count,
        v_normalized_count,
        v_rejected_count,
        v_geography_version,
        v_reasons;
END
$$;


--
-- Name: refresh_plr_population(text, integer); Type: FUNCTION; Schema: normalized; Owner: -
--

CREATE FUNCTION normalized.refresh_plr_population(p_source_sha256 text, p_expected_row_count integer DEFAULT 542) RETURNS TABLE(source_row_count bigint, accepted_row_count bigint, rejected_row_count bigint, rejection_reasons jsonb, reference_date date)
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_source_count BIGINT;
    v_distinct_plr_count BIGINT;
    v_blank_plr_count BIGINT;
    v_reference_code_count BIGINT;
    v_reference_code TEXT;
    v_reference_date DATE;
    v_accepted_count BIGINT;
    v_rejected_count BIGINT;
    v_reasons JSONB;
BEGIN
    SELECT
        COUNT(*),
        COUNT(DISTINCT NULLIF(BTRIM(plr_id_source), '')),
        COUNT(*) FILTER (
            WHERE NULLIF(BTRIM(plr_id_source), '') IS NULL
        ),
        COUNT(DISTINCT reference_code_source),
        MIN(reference_code_source)
    INTO
        v_source_count,
        v_distinct_plr_count,
        v_blank_plr_count,
        v_reference_code_count,
        v_reference_code
    FROM raw.afs_population
    WHERE source_sha256 = p_source_sha256;

    IF v_source_count = 0 THEN
        RAISE EXCEPTION
            'No raw AfS population rows found for source_sha256 %',
            p_source_sha256;
    END IF;

    IF v_source_count <> p_expected_row_count THEN
        RAISE EXCEPTION
            'AfS population source row count mismatch: expected %, got %',
            p_expected_row_count,
            v_source_count;
    END IF;

    IF v_blank_plr_count <> 0 THEN
        RAISE EXCEPTION
            'AfS population source contains % blank or null PLR IDs',
            v_blank_plr_count;
    END IF;

    IF v_distinct_plr_count <> v_source_count THEN
        RAISE EXCEPTION
            'AfS population source contains duplicate PLR IDs';
    END IF;

    IF v_reference_code_count <> 1 THEN
        RAISE EXCEPTION
            'AfS population source must contain exactly one reference code; got %',
            v_reference_code_count;
    END IF;

    IF v_reference_code !~ '^[0-9]{6}$' THEN
        RAISE EXCEPTION
            'AfS population reference code must have YYYYMM format; got %',
            v_reference_code;
    END IF;

    v_reference_date := (
        TO_DATE(v_reference_code || '01', 'YYYYMMDD')
        + INTERVAL '1 month'
        - INTERVAL '1 day'
    )::DATE;

    DELETE FROM normalized.plr_population_65plus AS accepted
    WHERE accepted.reference_date = v_reference_date;

    DELETE FROM normalized.plr_population_rejected AS rejected
    WHERE rejected.reference_date = v_reference_date;

    INSERT INTO normalized.plr_population_65plus (
        plr_id,
        population_total,
        population_65_79,
        population_80plus,
        population_65plus,
        share_65plus,
        reference_date,
        publication_date,
        source_sha256
    )
    SELECT
        classified.plr_id,
        classified.population_total,
        classified.population_65_79,
        classified.population_80plus,
        classified.population_65plus,
        classified.share_65plus,
        classified.reference_date,
        classified.publication_date,
        classified.source_sha256
    FROM normalized.classify_afs_population(p_source_sha256) AS classified
    WHERE classified.rejection_reason IS NULL;

    GET DIAGNOSTICS v_accepted_count = ROW_COUNT;

    INSERT INTO normalized.plr_population_rejected (
        plr_id,
        population_total,
        population_65_79,
        population_80plus,
        population_65plus,
        share_65plus,
        rejection_reason,
        reference_date,
        publication_date,
        rejected_at_utc,
        source_sha256
    )
    SELECT
        classified.plr_id,
        classified.population_total,
        classified.population_65_79,
        classified.population_80plus,
        classified.population_65plus,
        classified.share_65plus,
        classified.rejection_reason,
        classified.reference_date,
        classified.publication_date,
        NOW(),
        classified.source_sha256
    FROM normalized.classify_afs_population(p_source_sha256) AS classified
    WHERE classified.rejection_reason IS NOT NULL;

    GET DIAGNOSTICS v_rejected_count = ROW_COUNT;

    IF v_accepted_count + v_rejected_count <> v_source_count THEN
        RAISE EXCEPTION
            'AfS population quality split failed accounting: '
            'source %, accepted %, rejected %',
            v_source_count,
            v_accepted_count,
            v_rejected_count;
    END IF;

    SELECT COALESCE(
        JSONB_OBJECT_AGG(counts.rejection_reason, counts.reason_count),
        '{}'::JSONB
    )
    INTO v_reasons
    FROM (
        SELECT
            rejected.rejection_reason,
            COUNT(*) AS reason_count
        FROM normalized.plr_population_rejected AS rejected
        WHERE rejected.source_sha256 = p_source_sha256
        GROUP BY rejection_reason
    ) AS counts;

    RETURN QUERY
    SELECT
        v_source_count,
        v_accepted_count,
        v_rejected_count,
        v_reasons,
        v_reference_date;
END
$_$;


--
-- Name: check_icon_d2_ruc_field_partition(timestamp with time zone, text, timestamp with time zone, integer); Type: FUNCTION; Schema: raw; Owner: -
--

CREATE FUNCTION raw.check_icon_d2_ruc_field_partition(p_run_time_utc timestamp with time zone, p_lead_time text, p_expected_valid_time_utc timestamp with time zone, p_expected_source_point_count integer DEFAULT 542040) RETURNS TABLE(passed boolean, source_indicator_count bigint, field_indicator_count bigint, total_retained_row_count bigint, expected_retained_row_count bigint, mask_cell_count bigint, missing_indicator_count bigint, unexpected_indicator_count bigint, wrong_source_point_count_indicator_count bigint, wrong_retained_row_count_indicator_count bigint, wrong_valid_time_indicator_count bigint, inconsistent_scope_count bigint, outside_mask_row_count bigint, null_retained_value_count bigint, per_indicator_row_counts jsonb)
    LANGUAGE sql STABLE
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: plr_weather_population; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.plr_weather_population (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    temperature_c double precision,
    population_total bigint,
    population_65plus bigint,
    share_65plus double precision,
    population_status text NOT NULL,
    population_rejection_reason text,
    source_grid_id text NOT NULL,
    population_reference_date date NOT NULL,
    population_publication_date date,
    population_source_sha256 text NOT NULL,
    apparent_temperature_shade_c double precision,
    CONSTRAINT plr_weather_population_population_status_check CHECK ((population_status = ANY (ARRAY['available'::text, 'rejected_source_record'::text])))
);


--
-- Name: current_plr_weather_population; Type: VIEW; Schema: analytical; Owner: -
--

CREATE VIEW analytical.current_plr_weather_population AS
 WITH latest_partition AS (
         SELECT final_row_1.run_time_utc,
            final_row_1.lead_time,
            min(final_row_1.valid_time_utc) AS valid_time_utc
           FROM analytical.plr_weather_population final_row_1
          GROUP BY final_row_1.run_time_utc, final_row_1.lead_time
          ORDER BY final_row_1.run_time_utc DESC, (min(final_row_1.valid_time_utc))
         LIMIT 1
        )
 SELECT final_row.plr_id,
    final_row.geography_version,
    final_row.run_time_utc,
    final_row.lead_time,
    final_row.valid_time_utc,
    final_row.temperature_c,
    final_row.population_total,
    final_row.population_65plus,
    final_row.share_65plus,
    final_row.population_status,
    final_row.population_rejection_reason,
    final_row.source_grid_id,
    final_row.population_reference_date,
    final_row.population_publication_date,
    final_row.population_source_sha256,
    final_row.apparent_temperature_shade_c,
    (final_row.apparent_temperature_shade_c - final_row.temperature_c) AS apparent_temperature_delta_c
   FROM (analytical.plr_weather_population final_row
     JOIN latest_partition latest ON (((latest.run_time_utc = final_row.run_time_utc) AND (latest.lead_time = final_row.lead_time))));


--
-- Name: hostrada_berlin_hourly_reference; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.hostrada_berlin_hourly_reference (
    calendar_month smallint NOT NULL,
    geography_version text NOT NULL,
    calendar_day smallint NOT NULL,
    local_hour smallint NOT NULL,
    sample_count smallint NOT NULL,
    temperature_median_c double precision NOT NULL,
    temperature_p90_c double precision NOT NULL,
    temperature_max_c double precision NOT NULL,
    apparent_temperature_median_c double precision NOT NULL,
    apparent_temperature_p90_c double precision NOT NULL,
    apparent_temperature_max_c double precision NOT NULL,
    CONSTRAINT hostrada_berlin_hourly_reference_calendar_day_check CHECK (((calendar_day >= 1) AND (calendar_day <= 31))),
    CONSTRAINT hostrada_berlin_hourly_reference_calendar_month_check CHECK (((calendar_month >= 1) AND (calendar_month <= 12))),
    CONSTRAINT hostrada_berlin_hourly_reference_check CHECK ((NOT ((calendar_month = 2) AND (calendar_day = 29)))),
    CONSTRAINT hostrada_berlin_hourly_reference_check1 CHECK (((temperature_median_c <= temperature_p90_c) AND (temperature_p90_c <= temperature_max_c))),
    CONSTRAINT hostrada_berlin_hourly_reference_check2 CHECK (((apparent_temperature_median_c <= apparent_temperature_p90_c) AND (apparent_temperature_p90_c <= apparent_temperature_max_c))),
    CONSTRAINT hostrada_berlin_hourly_reference_local_hour_check CHECK (((local_hour >= 0) AND (local_hour <= 23))),
    CONSTRAINT hostrada_berlin_hourly_reference_sample_count_check CHECK ((sample_count > 0))
);


--
-- Name: hostrada_plr_hourly_reference; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.hostrada_plr_hourly_reference (
    calendar_month smallint NOT NULL,
    geography_version text NOT NULL,
    plr_id text NOT NULL,
    calendar_day smallint NOT NULL,
    local_hour smallint NOT NULL,
    sample_count smallint NOT NULL,
    temperature_median_c double precision NOT NULL,
    temperature_p90_c double precision NOT NULL,
    temperature_max_c double precision NOT NULL,
    apparent_temperature_median_c double precision NOT NULL,
    apparent_temperature_p90_c double precision NOT NULL,
    apparent_temperature_max_c double precision NOT NULL,
    CONSTRAINT hostrada_plr_hourly_reference_calendar_day_check CHECK (((calendar_day >= 1) AND (calendar_day <= 31))),
    CONSTRAINT hostrada_plr_hourly_reference_calendar_month_check CHECK (((calendar_month >= 1) AND (calendar_month <= 12))),
    CONSTRAINT hostrada_plr_hourly_reference_check CHECK ((NOT ((calendar_month = 2) AND (calendar_day = 29)))),
    CONSTRAINT hostrada_plr_hourly_reference_check1 CHECK (((temperature_median_c <= temperature_p90_c) AND (temperature_p90_c <= temperature_max_c))),
    CONSTRAINT hostrada_plr_hourly_reference_check2 CHECK (((apparent_temperature_median_c <= apparent_temperature_p90_c) AND (apparent_temperature_p90_c <= apparent_temperature_max_c))),
    CONSTRAINT hostrada_plr_hourly_reference_local_hour_check CHECK (((local_hour >= 0) AND (local_hour <= 23))),
    CONSTRAINT hostrada_plr_hourly_reference_sample_count_check CHECK ((sample_count > 0))
);


-- Analyst-facing labels are deliberately separate from engineering PLR facts.
CREATE TABLE analytical.plr_display_name (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    plr_name text NOT NULL,
    CONSTRAINT plr_display_name_plr_id_check CHECK ((plr_id ~ '^[0-9]{8}$'::text)),
    CONSTRAINT plr_display_name_plr_name_check CHECK ((btrim(plr_name) <> ''::text))
);


--
-- Name: plr_weather_context; Type: VIEW; Schema: analytical; Owner: -
--

CREATE VIEW analytical.plr_weather_context AS
 WITH local_forecasts AS (
         SELECT weather_row.plr_id,
            weather_row.geography_version,
            weather_row.run_time_utc,
            weather_row.lead_time,
            weather_row.valid_time_utc,
            weather_row.temperature_c,
            weather_row.population_total,
            weather_row.population_65plus,
            weather_row.share_65plus,
            weather_row.population_status,
            weather_row.population_rejection_reason,
            weather_row.source_grid_id,
            weather_row.population_reference_date,
            weather_row.population_publication_date,
            weather_row.population_source_sha256,
            weather_row.apparent_temperature_shade_c,
            (weather_row.valid_time_utc AT TIME ZONE 'Europe/Berlin'::text) AS valid_time_berlin
           FROM analytical.plr_weather_population weather_row
        )
 SELECT forecast.plr_id,
    display_name.plr_name,
    forecast.run_time_utc,
    forecast.lead_time,
    forecast.valid_time_utc,
    forecast.valid_time_berlin,
    forecast.temperature_c,
    forecast.apparent_temperature_shade_c,
    plr_reference.temperature_median_c AS plr_temperature_median_c,
    plr_reference.temperature_p90_c AS plr_temperature_p90_c,
    plr_reference.temperature_max_c AS plr_temperature_max_c,
    plr_reference.apparent_temperature_median_c AS plr_apparent_temperature_median_c,
    plr_reference.apparent_temperature_p90_c AS plr_apparent_temperature_p90_c,
    plr_reference.apparent_temperature_max_c AS plr_apparent_temperature_max_c,
    berlin_reference.temperature_median_c AS berlin_temperature_median_c,
    berlin_reference.temperature_p90_c AS berlin_temperature_p90_c,
    berlin_reference.temperature_max_c AS berlin_temperature_max_c,
    berlin_reference.apparent_temperature_median_c AS berlin_apparent_temperature_median_c,
    berlin_reference.apparent_temperature_p90_c AS berlin_apparent_temperature_p90_c,
    berlin_reference.apparent_temperature_max_c AS berlin_apparent_temperature_max_c,
    forecast.population_total,
    forecast.population_65plus,
    forecast.population_status
   FROM (((local_forecasts forecast
     LEFT JOIN analytical.plr_display_name display_name ON (((display_name.plr_id = forecast.plr_id) AND (display_name.geography_version = forecast.geography_version))))
     LEFT JOIN analytical.hostrada_plr_hourly_reference plr_reference ON (((plr_reference.calendar_month = (EXTRACT(month FROM forecast.valid_time_berlin))::smallint) AND (plr_reference.geography_version = forecast.geography_version) AND (plr_reference.plr_id = forecast.plr_id) AND (plr_reference.calendar_day = (EXTRACT(day FROM forecast.valid_time_berlin))::smallint) AND (plr_reference.local_hour = (EXTRACT(hour FROM forecast.valid_time_berlin))::smallint))))
     LEFT JOIN analytical.hostrada_berlin_hourly_reference berlin_reference ON (((berlin_reference.calendar_month = (EXTRACT(month FROM forecast.valid_time_berlin))::smallint) AND (berlin_reference.geography_version = forecast.geography_version) AND (berlin_reference.calendar_day = (EXTRACT(day FROM forecast.valid_time_berlin))::smallint) AND (berlin_reference.local_hour = (EXTRACT(hour FROM forecast.valid_time_berlin))::smallint))));


--
-- Name: current_plr_weather_context; Type: VIEW; Schema: analytical; Owner: -
--

CREATE VIEW analytical.current_plr_weather_context AS
 SELECT context_row.plr_id,
    context_row.plr_name,
    context_row.run_time_utc,
    context_row.lead_time,
    context_row.valid_time_utc,
    context_row.valid_time_berlin,
    context_row.temperature_c,
    context_row.apparent_temperature_shade_c,
    context_row.plr_temperature_median_c,
    context_row.plr_temperature_p90_c,
    context_row.plr_temperature_max_c,
    context_row.plr_apparent_temperature_median_c,
    context_row.plr_apparent_temperature_p90_c,
    context_row.plr_apparent_temperature_max_c,
    context_row.berlin_temperature_median_c,
    context_row.berlin_temperature_p90_c,
    context_row.berlin_temperature_max_c,
    context_row.berlin_apparent_temperature_median_c,
    context_row.berlin_apparent_temperature_p90_c,
    context_row.berlin_apparent_temperature_max_c,
    context_row.population_total,
    context_row.population_65plus,
    context_row.population_status
   FROM (analytical.plr_weather_context context_row
     JOIN ( SELECT current_row.run_time_utc,
            current_row.lead_time
           FROM analytical.current_plr_weather_population current_row
         LIMIT 1) current_partition ON (((current_partition.run_time_utc = context_row.run_time_utc) AND (current_partition.lead_time = context_row.lead_time))));


-- The 25-point extension reads forecast facts and precomputed reference
-- medians only; historical HOSTRADA observations are deliberately excluded.
CREATE VIEW analytical.current_plr_temperature_forecast_25h AS
WITH expected_leads AS (
    SELECT
        lead_hour,
        'PT' || lpad(lead_hour::text, 3, '0') || 'H00M' AS lead_time
    FROM generate_series(0, 24) AS expected(lead_hour)
),
expected_plr_count AS (
    SELECT COUNT(*)::bigint AS plr_count
    FROM analytical.plr_display_name
),
latest_complete_run AS (
    SELECT forecast.run_time_utc
    FROM analytical.plr_weather_population AS forecast
    JOIN expected_leads AS expected
      ON expected.lead_time = forecast.lead_time
    CROSS JOIN expected_plr_count
    GROUP BY forecast.run_time_utc, expected_plr_count.plr_count
    HAVING expected_plr_count.plr_count > 0
       AND COUNT(*) = expected_plr_count.plr_count * 25
       AND COUNT(DISTINCT forecast.plr_id) = expected_plr_count.plr_count
       AND COUNT(DISTINCT forecast.lead_time) = 25
    ORDER BY forecast.run_time_utc DESC
    LIMIT 1
)
SELECT
    forecast.plr_id,
    forecast.plr_name,
    forecast.run_time_utc AT TIME ZONE 'Europe/Berlin' AS run_time_berlin,
    expected.lead_hour::integer AS lead_hour,
    forecast.valid_time_berlin,
    forecast.temperature_c AS forecast_temperature_c,
    forecast.plr_temperature_median_c AS historical_temperature_median_c,
    forecast.temperature_c - forecast.plr_temperature_median_c
        AS temperature_difference_c,
    forecast.population_total,
    forecast.population_65plus,
    forecast.population_status
FROM analytical.plr_weather_context AS forecast
JOIN latest_complete_run AS current_run
  ON current_run.run_time_utc = forecast.run_time_utc
JOIN expected_leads AS expected
  ON expected.lead_time = forecast.lead_time;


CREATE VIEW analytical.current_plr_temperature_summary_25h AS
WITH ranked_forecasts AS (
    SELECT
        forecast.*,
        ROW_NUMBER() OVER (
            PARTITION BY forecast.plr_id
            ORDER BY
                forecast.forecast_temperature_c DESC NULLS LAST,
                forecast.valid_time_berlin ASC
        ) AS temperature_rank,
        ROW_NUMBER() OVER (
            PARTITION BY forecast.plr_id
            ORDER BY
                forecast.temperature_difference_c DESC NULLS LAST,
                forecast.valid_time_berlin ASC
        ) AS difference_rank
    FROM analytical.current_plr_temperature_forecast_25h AS forecast
)
SELECT
    forecast.plr_id,
    MAX(forecast.plr_name) AS plr_name,
    MIN(forecast.run_time_berlin) AS run_time_berlin,
    MAX(forecast.forecast_temperature_c) AS max_forecast_temperature_c,
    MIN(forecast.valid_time_berlin) FILTER (
        WHERE forecast.temperature_rank = 1
    ) AS max_forecast_temperature_at_berlin,
    MAX(forecast.temperature_difference_c) AS max_temperature_difference_c,
    MIN(forecast.valid_time_berlin) FILTER (
        WHERE forecast.difference_rank = 1
    ) AS max_temperature_difference_at_berlin,
    SUM(forecast.temperature_difference_c) AS sum_temperature_difference_c,
    MAX(forecast.population_total) AS population_total,
    MAX(forecast.population_65plus) AS population_65plus,
    MAX(forecast.population_status) AS population_status
FROM ranked_forecasts AS forecast
GROUP BY forecast.plr_id
HAVING COUNT(*) = 25
   AND COUNT(forecast.plr_name) = 25
   AND COUNT(forecast.forecast_temperature_c) = 25
   AND COUNT(forecast.historical_temperature_median_c) = 25;


--
-- Name: hostrada_berlin_hourly; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.hostrada_berlin_hourly (
    source_month_utc date NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    temperature_c double precision NOT NULL,
    apparent_temperature_shade_c double precision NOT NULL
);


--
-- Name: hostrada_plr_hourly; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.hostrada_plr_hourly (
    source_month_utc date NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    temperature_c double precision NOT NULL,
    apparent_temperature_shade_c double precision NOT NULL
);


-- Optional historical-year trajectories never enter the compact reference.
CREATE TABLE analytical.plr_temperature_history_25h (
    run_time_utc timestamp with time zone NOT NULL,
    plr_id text NOT NULL,
    lead_hour smallint NOT NULL,
    historical_year smallint NOT NULL,
    historical_valid_time_utc timestamp with time zone NOT NULL,
    historical_temperature_c double precision NOT NULL,
    CONSTRAINT plr_temperature_history_25h_lead_hour_check
        CHECK (lead_hour BETWEEN 0 AND 24),
    CONSTRAINT plr_temperature_history_25h_historical_year_check
        CHECK (historical_year BETWEEN 1995 AND 2025),
    CONSTRAINT plr_temperature_history_25h_pkey
        PRIMARY KEY (run_time_utc, plr_id, historical_year, lead_hour)
);


CREATE FUNCTION analytical.refresh_plr_temperature_history_25h(
    requested_run_time_utc timestamp with time zone
)
RETURNS TABLE (
    plr_count integer,
    historical_year_count integer,
    lead_hour_count integer,
    historical_row_count bigint,
    reused_existing boolean
)
LANGUAGE plpgsql
AS $function$
DECLARE
    expected_plr_count integer;
    expected_forecast_count integer;
    expected_history_count bigint;
    installed_history_count bigint;
BEGIN
    IF requested_run_time_utc IS NULL THEN
        RAISE EXCEPTION 'A forecast run time in UTC is required.';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext('analytical.plr_temperature_history_25h')
    );

    SELECT
        COUNT(DISTINCT forecast.plr_id)::integer,
        COUNT(*)::integer
    INTO expected_plr_count, expected_forecast_count
    FROM analytical.current_plr_temperature_forecast_25h AS forecast
    WHERE forecast.run_time_berlin =
        requested_run_time_utc AT TIME ZONE 'Europe/Berlin';

    IF expected_plr_count < 1
       OR expected_forecast_count <> expected_plr_count * 25
    THEN
        RAISE EXCEPTION
            'The requested run is not the current complete 25-point forecast.';
    END IF;

    expected_history_count := expected_plr_count::bigint * 25 * 31;

    SELECT COUNT(*)
    INTO installed_history_count
    FROM analytical.plr_temperature_history_25h AS history
    WHERE history.run_time_utc = requested_run_time_utc;

    IF installed_history_count = expected_history_count THEN
        DELETE FROM analytical.plr_temperature_history_25h AS history
        WHERE history.run_time_utc <> requested_run_time_utc;

        RETURN QUERY
        SELECT
            expected_plr_count,
            31,
            25,
            installed_history_count,
            true;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM analytical.hostrada_plr_hourly AS hourly
        WHERE hourly.source_month_utc >= DATE '1995-01-01'
          AND hourly.source_month_utc < DATE '2026-01-01'
    ) THEN
        RAISE EXCEPTION
            'Historical trajectories require the original 1995-2025 HOSTRADA hourly observations; the compact reference snapshot is insufficient.';
    END IF;

    DELETE FROM analytical.plr_temperature_history_25h;

    WITH selected_forecast AS MATERIALIZED (
        SELECT
            forecast.plr_id,
            forecast.lead_hour,
            forecast.valid_time_berlin
        FROM analytical.current_plr_temperature_forecast_25h AS forecast
        WHERE forecast.run_time_berlin =
            requested_run_time_utc AT TIME ZONE 'Europe/Berlin'
    ),
    forecast_hours AS MATERIALIZED (
        SELECT DISTINCT
            forecast.lead_hour,
            forecast.valid_time_berlin
        FROM selected_forecast AS forecast
    ),
    forecast_plrs AS MATERIALIZED (
        SELECT DISTINCT forecast.plr_id
        FROM selected_forecast AS forecast
    ),
    historical_local_hours AS MATERIALIZED (
        SELECT
            forecast.lead_hour,
            historical.year::smallint AS historical_year,
            make_timestamp(
                historical.year,
                EXTRACT(MONTH FROM forecast.valid_time_berlin)::integer,
                EXTRACT(DAY FROM forecast.valid_time_berlin)::integer,
                EXTRACT(HOUR FROM forecast.valid_time_berlin)::integer,
                0,
                0
            ) AS historical_valid_time_berlin
        FROM forecast_hours AS forecast
        CROSS JOIN generate_series(1995, 2025) AS historical(year)
    ),
    historical_utc_hours AS MATERIALIZED (
        SELECT
            target.lead_hour,
            target.historical_year,
            target.historical_valid_time_berlin,
            target.historical_valid_time_berlin
                AT TIME ZONE 'Europe/Berlin' AS historical_valid_time_utc
        FROM historical_local_hours AS target
    ),
    indexed_source_lookups AS MATERIALIZED (
        SELECT
            target.lead_hour,
            target.historical_year,
            target.historical_valid_time_utc,
            date_trunc(
                'month',
                target.historical_valid_time_utc AT TIME ZONE 'UTC'
            )::date AS source_month_utc
        FROM historical_utc_hours AS target
        WHERE target.historical_valid_time_utc AT TIME ZONE 'Europe/Berlin'
            = target.historical_valid_time_berlin
    )
    INSERT INTO analytical.plr_temperature_history_25h (
        run_time_utc,
        plr_id,
        lead_hour,
        historical_year,
        historical_valid_time_utc,
        historical_temperature_c
    )
    SELECT
        requested_run_time_utc,
        hourly.plr_id,
        target.lead_hour,
        target.historical_year,
        hourly.valid_time_utc,
        hourly.temperature_c
    FROM indexed_source_lookups AS target
    CROSS JOIN LATERAL (
        SELECT
            source.plr_id,
            source.valid_time_utc,
            source.temperature_c
        FROM analytical.hostrada_plr_hourly AS source
        WHERE source.source_month_utc = target.source_month_utc
          AND source.valid_time_utc = target.historical_valid_time_utc
        -- Preserve one parameterized index lookup per historical timestamp.
        OFFSET 0
    ) AS hourly
    JOIN forecast_plrs AS geography
      ON geography.plr_id = hourly.plr_id;

    GET DIAGNOSTICS installed_history_count = ROW_COUNT;

    IF installed_history_count <> expected_history_count THEN
        RAISE EXCEPTION
            'Historical trajectory extraction returned % rows; expected % for % PLRs, 25 lead hours, and 31 historical years.',
            installed_history_count,
            expected_history_count,
            expected_plr_count;
    END IF;

    RETURN QUERY
    SELECT
        expected_plr_count,
        31,
        25,
        installed_history_count,
        false;
END;
$function$;


CREATE VIEW analytical.current_plr_temperature_history_25h AS
SELECT
    forecast.plr_id,
    forecast.plr_name,
    forecast.run_time_berlin,
    forecast.lead_hour,
    forecast.valid_time_berlin,
    history.historical_year,
    history.historical_valid_time_utc AT TIME ZONE 'Europe/Berlin'
        AS historical_valid_time_berlin,
    history.historical_temperature_c,
    forecast.forecast_temperature_c,
    forecast.historical_temperature_median_c
FROM analytical.current_plr_temperature_forecast_25h AS forecast
JOIN analytical.plr_temperature_history_25h AS history
  ON history.run_time_utc = forecast.run_time_berlin
        AT TIME ZONE 'Europe/Berlin'
 AND history.plr_id = forecast.plr_id
 AND history.lead_hour = forecast.lead_hour;


--
-- Name: plr_weather; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.plr_weather (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    temperature_c double precision,
    source_grid_id text NOT NULL,
    apparent_temperature_shade_c double precision
);


--
-- Name: plr_weather_population_rejected; Type: TABLE; Schema: analytical; Owner: -
--

CREATE TABLE analytical.plr_weather_population_rejected (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    rejection_reason text NOT NULL,
    rejected_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    population_reference_date date,
    rejection_details jsonb
);


--
-- Name: hostrada_cell; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.hostrada_cell (
    source_grid_id text NOT NULL,
    geography_version text NOT NULL,
    y_index integer NOT NULL,
    x_index integer NOT NULL,
    geometry public.geometry(Polygon,25833) NOT NULL,
    hostrada_cell_area_m2 double precision NOT NULL,
    CONSTRAINT hostrada_cell_geography_version_check CHECK ((btrim(geography_version) <> ''::text)),
    CONSTRAINT hostrada_cell_geometry_check CHECK (public.st_isvalid(geometry)),
    CONSTRAINT hostrada_cell_geometry_check1 CHECK ((NOT public.st_isempty(geometry))),
    CONSTRAINT hostrada_cell_hostrada_cell_area_m2_check CHECK ((hostrada_cell_area_m2 > (0)::double precision)),
    CONSTRAINT hostrada_cell_x_index_check CHECK ((x_index >= 0)),
    CONSTRAINT hostrada_cell_y_index_check CHECK ((y_index >= 0))
);


--
-- Name: hostrada_grid; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.hostrada_grid (
    source_grid_id text NOT NULL,
    grid_fingerprint text NOT NULL,
    dataset_version text NOT NULL,
    source_srid integer NOT NULL,
    target_srid integer NOT NULL,
    x_origin_m double precision NOT NULL,
    y_origin_m double precision NOT NULL,
    x_count integer NOT NULL,
    y_count integer NOT NULL,
    x_spacing_m double precision NOT NULL,
    y_spacing_m double precision NOT NULL,
    registered_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hostrada_grid_dataset_version_check CHECK ((btrim(dataset_version) <> ''::text)),
    CONSTRAINT hostrada_grid_grid_fingerprint_check CHECK ((grid_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT hostrada_grid_source_grid_id_check CHECK ((btrim(source_grid_id) <> ''::text)),
    CONSTRAINT hostrada_grid_source_srid_check CHECK ((source_srid = 3034)),
    CONSTRAINT hostrada_grid_target_srid_check CHECK ((target_srid = 25833)),
    CONSTRAINT hostrada_grid_x_count_check CHECK ((x_count > 0)),
    CONSTRAINT hostrada_grid_x_spacing_m_check CHECK ((x_spacing_m > (0)::double precision)),
    CONSTRAINT hostrada_grid_y_count_check CHECK ((y_count > 0)),
    CONSTRAINT hostrada_grid_y_spacing_m_check CHECK ((y_spacing_m > (0)::double precision))
);


--
-- Name: hostrada_plr_area_bridge; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.hostrada_plr_area_bridge (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    y_index integer NOT NULL,
    x_index integer NOT NULL,
    intersection_area_m2 double precision NOT NULL,
    plr_area_m2 double precision NOT NULL,
    hostrada_cell_area_m2 double precision NOT NULL,
    fraction_of_plr double precision NOT NULL,
    fraction_of_hostrada_cell double precision NOT NULL,
    CONSTRAINT hostrada_plr_area_bridge_fraction_of_hostrada_cell_check CHECK ((fraction_of_hostrada_cell > (0)::double precision)),
    CONSTRAINT hostrada_plr_area_bridge_fraction_of_plr_check CHECK ((fraction_of_plr > (0)::double precision)),
    CONSTRAINT hostrada_plr_area_bridge_hostrada_cell_area_m2_check CHECK ((hostrada_cell_area_m2 > (0)::double precision)),
    CONSTRAINT hostrada_plr_area_bridge_intersection_area_m2_check CHECK ((intersection_area_m2 > (0)::double precision)),
    CONSTRAINT hostrada_plr_area_bridge_plr_area_m2_check CHECK ((plr_area_m2 > (0)::double precision))
);


--
-- Name: icon_cell; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.icon_cell (
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    geometry public.geometry(Polygon,25833) NOT NULL,
    icon_cell_area_m2 double precision NOT NULL,
    CONSTRAINT icon_cell_icon_cell_area_m2_check CHECK ((icon_cell_area_m2 > (0)::double precision)),
    CONSTRAINT normalized_icon_cell_geometry_valid CHECK (public.st_isvalid(geometry))
);


--
-- Name: icon_d2_ruc_weather; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.icon_d2_ruc_weather (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    cell_index integer NOT NULL,
    temperature_c double precision,
    source_grid_id text NOT NULL,
    geography_version text NOT NULL,
    mask_buffer_m integer NOT NULL,
    apparent_temperature_shade_c double precision,
    CONSTRAINT icon_d2_ruc_weather_mask_buffer_nonnegative CHECK ((mask_buffer_m >= 0))
);


--
-- Name: icon_geometry_rejected; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.icon_geometry_rejected (
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    rejection_reason text NOT NULL,
    rejected_at_utc timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: icon_plr_area_bridge; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.icon_plr_area_bridge (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    intersection_area_m2 double precision NOT NULL,
    plr_area_m2 double precision NOT NULL,
    icon_cell_area_m2 double precision NOT NULL,
    fraction_of_plr double precision NOT NULL,
    fraction_of_icon_cell double precision NOT NULL,
    CONSTRAINT icon_plr_area_bridge_fraction_of_icon_cell_check CHECK ((fraction_of_icon_cell > (0)::double precision)),
    CONSTRAINT icon_plr_area_bridge_fraction_of_plr_check CHECK ((fraction_of_plr > (0)::double precision)),
    CONSTRAINT icon_plr_area_bridge_icon_cell_area_m2_check CHECK ((icon_cell_area_m2 > (0)::double precision)),
    CONSTRAINT icon_plr_area_bridge_intersection_area_m2_check CHECK ((intersection_area_m2 > (0)::double precision)),
    CONSTRAINT icon_plr_area_bridge_plr_area_m2_check CHECK ((plr_area_m2 > (0)::double precision))
);


--
-- Name: icon_weather_mask; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.icon_weather_mask (
    geography_version text NOT NULL,
    source_grid_id text NOT NULL,
    mask_buffer_m integer NOT NULL,
    cell_index integer NOT NULL,
    created_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT icon_weather_mask_mask_buffer_m_check CHECK ((mask_buffer_m >= 0))
);


--
-- Name: plr; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.plr (
    plr_id text NOT NULL,
    geometry public.geometry(MultiPolygon,25833) NOT NULL,
    geography_version text NOT NULL,
    reference_date date,
    source_sha256 text NOT NULL,
    CONSTRAINT normalized_plr_geometry_positive_area CHECK ((public.st_area(geometry) > (0)::double precision)),
    CONSTRAINT normalized_plr_geometry_valid CHECK (public.st_isvalid(geometry))
);


--
-- Name: plr_geometry_rejected; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.plr_geometry_rejected (
    source_sha256 text NOT NULL,
    source_row_id bigint NOT NULL,
    plr_id text,
    geography_version text,
    rejection_reason text NOT NULL,
    rejected_at_utc timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plr_population_65plus; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.plr_population_65plus (
    plr_id text NOT NULL,
    population_total bigint NOT NULL,
    population_65_79 bigint NOT NULL,
    population_80plus bigint NOT NULL,
    population_65plus bigint NOT NULL,
    share_65plus double precision NOT NULL,
    reference_date date NOT NULL,
    publication_date date,
    source_sha256 text NOT NULL,
    CONSTRAINT plr_population_65plus_check CHECK ((population_65plus <= population_total)),
    CONSTRAINT plr_population_65plus_population_65_79_check CHECK ((population_65_79 >= 0)),
    CONSTRAINT plr_population_65plus_population_65plus_check CHECK ((population_65plus >= 0)),
    CONSTRAINT plr_population_65plus_population_80plus_check CHECK ((population_80plus >= 0)),
    CONSTRAINT plr_population_65plus_population_total_check CHECK ((population_total >= 0)),
    CONSTRAINT plr_population_65plus_share_65plus_check CHECK (((share_65plus >= (0)::double precision) AND (share_65plus <= (1)::double precision)))
);


--
-- Name: plr_population_rejected; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.plr_population_rejected (
    plr_id text NOT NULL,
    population_total bigint,
    population_65_79 bigint,
    population_80plus bigint,
    population_65plus bigint,
    share_65plus double precision,
    rejection_reason text NOT NULL,
    reference_date date NOT NULL,
    publication_date date,
    rejected_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    source_sha256 text NOT NULL
);


--
-- Name: weather_partition_rejected; Type: TABLE; Schema: normalized; Owner: -
--

CREATE TABLE normalized.weather_partition_rejected (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    rejection_reason text NOT NULL,
    observed_indicators jsonb,
    observed_row_counts jsonb,
    rejected_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    source_grid_id text,
    geography_version text,
    mask_buffer_m integer,
    rejection_details jsonb
);


--
-- Name: afs_population; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.afs_population (
    source_row_id bigint NOT NULL,
    plr_id_source text,
    population_total_source text,
    population_65_79_source text,
    population_80plus_source text,
    reference_date date,
    publication_date date,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    reference_code_source text,
    source_url text,
    publisher text
);


--
-- Name: afs_population_source_row_id_seq; Type: SEQUENCE; Schema: raw; Owner: -
--

CREATE SEQUENCE raw.afs_population_source_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: afs_population_source_row_id_seq; Type: SEQUENCE OWNED BY; Schema: raw; Owner: -
--

ALTER SEQUENCE raw.afs_population_source_row_id_seq OWNED BY raw.afs_population.source_row_id;


--
-- Name: hostrada_month_source; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.hostrada_month_source (
    source_month_utc date NOT NULL,
    variable_name text NOT NULL,
    source_grid_id text NOT NULL,
    source_url text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_size_bytes bigint NOT NULL,
    source_unit text NOT NULL,
    first_valid_time_utc timestamp with time zone NOT NULL,
    last_valid_time_utc timestamp with time zone NOT NULL,
    source_hour_count integer NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    source_deleted_at_utc timestamp with time zone,
    CONSTRAINT hostrada_month_source_check CHECK ((last_valid_time_utc >= first_valid_time_utc)),
    CONSTRAINT hostrada_month_source_source_hour_count_check CHECK ((source_hour_count > 0)),
    CONSTRAINT hostrada_month_source_source_month_utc_check CHECK ((source_month_utc = (date_trunc('month'::text, (source_month_utc)::timestamp without time zone))::date)),
    CONSTRAINT hostrada_month_source_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT hostrada_month_source_source_size_bytes_check CHECK ((source_size_bytes > 0)),
    CONSTRAINT hostrada_month_source_variable_name_check CHECK ((variable_name = ANY (ARRAY['tas'::text, 'hurs'::text, 'sfcWind'::text])))
);


--
-- Name: COLUMN hostrada_month_source.source_deleted_at_utc; Type: COMMENT; Schema: raw; Owner: -
--

COMMENT ON COLUMN raw.hostrada_month_source.source_deleted_at_utc IS 'Time at which the validated local source file was removed after both monthly analytical outputs passed their completeness check.';


--
-- Name: icon_d2_ruc_field; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.icon_d2_ruc_field (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    indicator text NOT NULL,
    cell_index integer NOT NULL,
    source_value double precision
);


--
-- Name: icon_d2_ruc_source; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.icon_d2_ruc_source (
    run_time_utc timestamp with time zone NOT NULL,
    lead_time text NOT NULL,
    indicator text NOT NULL,
    valid_time_utc timestamp with time zone NOT NULL,
    source_grid_id text NOT NULL,
    geography_version text NOT NULL,
    mask_buffer_m integer NOT NULL,
    source_unit text NOT NULL,
    source_url text NOT NULL,
    raw_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_point_count integer NOT NULL,
    source_missing_value_count integer NOT NULL,
    retained_point_count integer NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT icon_d2_ruc_source_check CHECK ((retained_point_count <= source_point_count)),
    CONSTRAINT icon_d2_ruc_source_mask_buffer_m_check CHECK ((mask_buffer_m >= 0)),
    CONSTRAINT icon_d2_ruc_source_retained_point_count_check CHECK ((retained_point_count > 0)),
    CONSTRAINT icon_d2_ruc_source_source_missing_value_count_check CHECK ((source_missing_value_count >= 0)),
    CONSTRAINT icon_d2_ruc_source_source_point_count_check CHECK ((source_point_count > 0))
);


--
-- Name: icon_grid_cell_vertex; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.icon_grid_cell_vertex (
    source_grid_id text NOT NULL,
    cell_index integer NOT NULL,
    vertex_order smallint NOT NULL,
    vertex_index integer NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: icon_grid_source; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.icon_grid_source (
    source_grid_id text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    vertex_count integer NOT NULL,
    cell_count integer NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT icon_grid_source_cell_count_check CHECK ((cell_count > 0)),
    CONSTRAINT icon_grid_source_vertex_count_check CHECK ((vertex_count > 0))
);


--
-- Name: icon_grid_vertex; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.icon_grid_vertex (
    source_grid_id text NOT NULL,
    vertex_index integer NOT NULL,
    longitude_deg double precision NOT NULL,
    latitude_deg double precision NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lor_plr; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.lor_plr (
    source_row_id bigint NOT NULL,
    plr_id_source text,
    geometry_source public.geometry,
    source_crs text,
    geography_version text,
    reference_date date,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    loaded_at_utc timestamp with time zone DEFAULT now() NOT NULL,
    source_url text,
    publisher text,
    license text
);


--
-- Name: lor_plr_source_row_id_seq; Type: SEQUENCE; Schema: raw; Owner: -
--

CREATE SEQUENCE raw.lor_plr_source_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lor_plr_source_row_id_seq; Type: SEQUENCE OWNED BY; Schema: raw; Owner: -
--

ALTER SEQUENCE raw.lor_plr_source_row_id_seq OWNED BY raw.lor_plr.source_row_id;


--
-- Name: afs_population source_row_id; Type: DEFAULT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.afs_population ALTER COLUMN source_row_id SET DEFAULT nextval('raw.afs_population_source_row_id_seq'::regclass);


--
-- Name: lor_plr source_row_id; Type: DEFAULT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.lor_plr ALTER COLUMN source_row_id SET DEFAULT nextval('raw.lor_plr_source_row_id_seq'::regclass);


--
-- Name: hostrada_berlin_hourly hostrada_berlin_hourly_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_berlin_hourly
    ADD CONSTRAINT hostrada_berlin_hourly_pkey PRIMARY KEY (source_month_utc, valid_time_utc);


--
-- Name: hostrada_berlin_hourly_reference hostrada_berlin_hourly_reference_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_berlin_hourly_reference
    ADD CONSTRAINT hostrada_berlin_hourly_reference_pkey PRIMARY KEY (calendar_month, geography_version, calendar_day, local_hour);


--
-- Name: hostrada_plr_hourly hostrada_plr_hourly_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_plr_hourly
    ADD CONSTRAINT hostrada_plr_hourly_pkey PRIMARY KEY (source_month_utc, valid_time_utc, plr_id);


--
-- Name: hostrada_plr_hourly_reference hostrada_plr_hourly_reference_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_plr_hourly_reference
    ADD CONSTRAINT hostrada_plr_hourly_reference_pkey PRIMARY KEY (calendar_month, geography_version, plr_id, calendar_day, local_hour);


ALTER TABLE ONLY analytical.plr_display_name
    ADD CONSTRAINT plr_display_name_pkey PRIMARY KEY (plr_id, geography_version);


--
-- Name: plr_weather plr_weather_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather
    ADD CONSTRAINT plr_weather_pkey PRIMARY KEY (run_time_utc, lead_time, plr_id, geography_version);


--
-- Name: plr_weather_population plr_weather_population_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_pkey PRIMARY KEY (run_time_utc, lead_time, plr_id, geography_version);


--
-- Name: plr_weather_population_rejected plr_weather_population_rejected_pkey; Type: CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather_population_rejected
    ADD CONSTRAINT plr_weather_population_rejected_pkey PRIMARY KEY (run_time_utc, lead_time, plr_id, geography_version);


--
-- Name: hostrada_cell hostrada_cell_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_cell
    ADD CONSTRAINT hostrada_cell_pkey PRIMARY KEY (source_grid_id, geography_version, y_index, x_index);


--
-- Name: hostrada_grid hostrada_grid_grid_fingerprint_key; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_grid
    ADD CONSTRAINT hostrada_grid_grid_fingerprint_key UNIQUE (grid_fingerprint);


--
-- Name: hostrada_grid hostrada_grid_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_grid
    ADD CONSTRAINT hostrada_grid_pkey PRIMARY KEY (source_grid_id);


--
-- Name: hostrada_plr_area_bridge hostrada_plr_area_bridge_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_plr_area_bridge
    ADD CONSTRAINT hostrada_plr_area_bridge_pkey PRIMARY KEY (plr_id, geography_version, source_grid_id, y_index, x_index);


--
-- Name: icon_cell icon_cell_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_cell
    ADD CONSTRAINT icon_cell_pkey PRIMARY KEY (source_grid_id, cell_index);


--
-- Name: icon_d2_ruc_weather icon_d2_ruc_weather_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_d2_ruc_weather
    ADD CONSTRAINT icon_d2_ruc_weather_pkey PRIMARY KEY (run_time_utc, lead_time, source_grid_id, geography_version, cell_index);


--
-- Name: icon_geometry_rejected icon_geometry_rejected_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_geometry_rejected
    ADD CONSTRAINT icon_geometry_rejected_pkey PRIMARY KEY (source_grid_id, cell_index);


--
-- Name: icon_plr_area_bridge icon_plr_area_bridge_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_plr_area_bridge
    ADD CONSTRAINT icon_plr_area_bridge_pkey PRIMARY KEY (plr_id, geography_version, source_grid_id, cell_index);


--
-- Name: icon_weather_mask icon_weather_mask_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_weather_mask
    ADD CONSTRAINT icon_weather_mask_pkey PRIMARY KEY (geography_version, source_grid_id, mask_buffer_m, cell_index);


--
-- Name: plr_geometry_rejected plr_geometry_rejected_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.plr_geometry_rejected
    ADD CONSTRAINT plr_geometry_rejected_pkey PRIMARY KEY (source_sha256, source_row_id);


--
-- Name: plr plr_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.plr
    ADD CONSTRAINT plr_pkey PRIMARY KEY (plr_id, geography_version);


--
-- Name: plr_population_65plus plr_population_65plus_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.plr_population_65plus
    ADD CONSTRAINT plr_population_65plus_pkey PRIMARY KEY (reference_date, plr_id);


--
-- Name: plr_population_rejected plr_population_rejected_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.plr_population_rejected
    ADD CONSTRAINT plr_population_rejected_pkey PRIMARY KEY (plr_id, reference_date);


--
-- Name: weather_partition_rejected weather_partition_rejected_pkey; Type: CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.weather_partition_rejected
    ADD CONSTRAINT weather_partition_rejected_pkey PRIMARY KEY (run_time_utc, lead_time);


--
-- Name: afs_population afs_population_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.afs_population
    ADD CONSTRAINT afs_population_pkey PRIMARY KEY (source_row_id);


--
-- Name: hostrada_month_source hostrada_month_source_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.hostrada_month_source
    ADD CONSTRAINT hostrada_month_source_pkey PRIMARY KEY (source_month_utc, variable_name);


--
-- Name: icon_d2_ruc_field icon_d2_ruc_field_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_d2_ruc_field
    ADD CONSTRAINT icon_d2_ruc_field_pkey PRIMARY KEY (run_time_utc, lead_time, indicator, cell_index);


--
-- Name: icon_d2_ruc_source icon_d2_ruc_source_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_d2_ruc_source
    ADD CONSTRAINT icon_d2_ruc_source_pkey PRIMARY KEY (run_time_utc, lead_time, indicator);


--
-- Name: icon_grid_cell_vertex icon_grid_cell_vertex_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_grid_cell_vertex
    ADD CONSTRAINT icon_grid_cell_vertex_pkey PRIMARY KEY (source_grid_id, cell_index, vertex_order);


--
-- Name: icon_grid_source icon_grid_source_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_grid_source
    ADD CONSTRAINT icon_grid_source_pkey PRIMARY KEY (source_grid_id);


--
-- Name: icon_grid_vertex icon_grid_vertex_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_grid_vertex
    ADD CONSTRAINT icon_grid_vertex_pkey PRIMARY KEY (source_grid_id, vertex_index);


--
-- Name: lor_plr lor_plr_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.lor_plr
    ADD CONSTRAINT lor_plr_pkey PRIMARY KEY (source_row_id);


--
-- Name: idx_analytical_plr_weather_partition; Type: INDEX; Schema: analytical; Owner: -
--

CREATE INDEX idx_analytical_plr_weather_partition ON analytical.plr_weather USING btree (run_time_utc, lead_time);


--
-- Name: idx_analytical_weather_population_partition; Type: INDEX; Schema: analytical; Owner: -
--

CREATE INDEX idx_analytical_weather_population_partition ON analytical.plr_weather_population USING btree (run_time_utc, lead_time);


--
-- Name: idx_hostrada_cell_geometry; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_hostrada_cell_geometry ON normalized.hostrada_cell USING gist (geometry);


--
-- Name: idx_hostrada_plr_bridge_cell; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_hostrada_plr_bridge_cell ON normalized.hostrada_plr_area_bridge USING btree (source_grid_id, geography_version, y_index, x_index);


--
-- Name: idx_icon_geometry_rejected_source; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_icon_geometry_rejected_source ON normalized.icon_geometry_rejected USING btree (source_grid_id);


--
-- Name: idx_icon_plr_area_bridge_icon_cell; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_icon_plr_area_bridge_icon_cell ON normalized.icon_plr_area_bridge USING btree (source_grid_id, cell_index);


--
-- Name: idx_icon_weather_mask_cell; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_icon_weather_mask_cell ON normalized.icon_weather_mask USING btree (source_grid_id, cell_index);


--
-- Name: idx_normalized_icon_cell_geometry; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_icon_cell_geometry ON normalized.icon_cell USING gist (geometry);


--
-- Name: idx_normalized_plr_geometry; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_plr_geometry ON normalized.plr USING gist (geometry);


--
-- Name: idx_normalized_plr_source_sha256; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_plr_source_sha256 ON normalized.plr USING btree (source_sha256);


--
-- Name: idx_normalized_population_rejected_source; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_population_rejected_source ON normalized.plr_population_rejected USING btree (source_sha256);


--
-- Name: idx_normalized_population_source; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_population_source ON normalized.plr_population_65plus USING btree (source_sha256);


--
-- Name: idx_normalized_weather_partition; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_weather_partition ON normalized.icon_d2_ruc_weather USING btree (run_time_utc, lead_time);


--
-- Name: idx_normalized_weather_scope; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_normalized_weather_scope ON normalized.icon_d2_ruc_weather USING btree (source_grid_id, geography_version, cell_index);


--
-- Name: idx_plr_geometry_rejected_source; Type: INDEX; Schema: normalized; Owner: -
--

CREATE INDEX idx_plr_geometry_rejected_source ON normalized.plr_geometry_rejected USING btree (source_sha256);


--
-- Name: idx_icon_grid_cell_vertex_vertex; Type: INDEX; Schema: raw; Owner: -
--

CREATE INDEX idx_icon_grid_cell_vertex_vertex ON raw.icon_grid_cell_vertex USING btree (source_grid_id, vertex_index);


--
-- Name: idx_raw_afs_population_source; Type: INDEX; Schema: raw; Owner: -
--

CREATE INDEX idx_raw_afs_population_source ON raw.afs_population USING btree (source_sha256);


--
-- Name: idx_raw_lor_source_sha256; Type: INDEX; Schema: raw; Owner: -
--

CREATE INDEX idx_raw_lor_source_sha256 ON raw.lor_plr USING btree (source_sha256);


--
-- Name: hostrada_berlin_hourly hostrada_berlin_hourly_source_grid_id_fkey; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_berlin_hourly
    ADD CONSTRAINT hostrada_berlin_hourly_source_grid_id_fkey FOREIGN KEY (source_grid_id) REFERENCES normalized.hostrada_grid(source_grid_id);


--
-- Name: hostrada_plr_hourly hostrada_plr_hourly_plr_id_geography_version_fkey; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_plr_hourly
    ADD CONSTRAINT hostrada_plr_hourly_plr_id_geography_version_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: hostrada_plr_hourly_reference hostrada_plr_hourly_reference_plr_id_geography_version_fkey; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_plr_hourly_reference
    ADD CONSTRAINT hostrada_plr_hourly_reference_plr_id_geography_version_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


ALTER TABLE ONLY analytical.plr_display_name
    ADD CONSTRAINT plr_display_name_plr_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: hostrada_plr_hourly hostrada_plr_hourly_source_grid_id_fkey; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.hostrada_plr_hourly
    ADD CONSTRAINT hostrada_plr_hourly_source_grid_id_fkey FOREIGN KEY (source_grid_id) REFERENCES normalized.hostrada_grid(source_grid_id);


--
-- Name: plr_weather plr_weather_plr_fk; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather
    ADD CONSTRAINT plr_weather_plr_fk FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: plr_weather_population plr_weather_population_plr_fk; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_plr_fk FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: plr_weather_population plr_weather_population_plr_id_geography_version_fkey; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_plr_id_geography_version_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: plr_weather_population plr_weather_population_weather_fk; Type: FK CONSTRAINT; Schema: analytical; Owner: -
--

ALTER TABLE ONLY analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_weather_fk FOREIGN KEY (run_time_utc, lead_time, plr_id, geography_version) REFERENCES analytical.plr_weather(run_time_utc, lead_time, plr_id, geography_version) ON DELETE CASCADE;


--
-- Name: hostrada_cell hostrada_cell_source_grid_id_fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_cell
    ADD CONSTRAINT hostrada_cell_source_grid_id_fkey FOREIGN KEY (source_grid_id) REFERENCES normalized.hostrada_grid(source_grid_id) ON DELETE CASCADE;


--
-- Name: hostrada_plr_area_bridge hostrada_plr_area_bridge_plr_id_geography_version_fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_plr_area_bridge
    ADD CONSTRAINT hostrada_plr_area_bridge_plr_id_geography_version_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: hostrada_plr_area_bridge hostrada_plr_area_bridge_source_grid_id_geography_version__fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.hostrada_plr_area_bridge
    ADD CONSTRAINT hostrada_plr_area_bridge_source_grid_id_geography_version__fkey FOREIGN KEY (source_grid_id, geography_version, y_index, x_index) REFERENCES normalized.hostrada_cell(source_grid_id, geography_version, y_index, x_index) ON DELETE CASCADE;


--
-- Name: icon_d2_ruc_weather icon_d2_ruc_weather_icon_cell_fk; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_d2_ruc_weather
    ADD CONSTRAINT icon_d2_ruc_weather_icon_cell_fk FOREIGN KEY (source_grid_id, cell_index) REFERENCES normalized.icon_cell(source_grid_id, cell_index);


--
-- Name: icon_geometry_rejected icon_geometry_rejected_source_grid_id_fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_geometry_rejected
    ADD CONSTRAINT icon_geometry_rejected_source_grid_id_fkey FOREIGN KEY (source_grid_id) REFERENCES raw.icon_grid_source(source_grid_id) ON DELETE CASCADE;


--
-- Name: icon_plr_area_bridge icon_plr_area_bridge_plr_id_geography_version_fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_plr_area_bridge
    ADD CONSTRAINT icon_plr_area_bridge_plr_id_geography_version_fkey FOREIGN KEY (plr_id, geography_version) REFERENCES normalized.plr(plr_id, geography_version);


--
-- Name: icon_plr_area_bridge icon_plr_area_bridge_source_grid_id_cell_index_fkey; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_plr_area_bridge
    ADD CONSTRAINT icon_plr_area_bridge_source_grid_id_cell_index_fkey FOREIGN KEY (source_grid_id, cell_index) REFERENCES normalized.icon_cell(source_grid_id, cell_index);


--
-- Name: icon_weather_mask icon_weather_mask_icon_cell_fk; Type: FK CONSTRAINT; Schema: normalized; Owner: -
--

ALTER TABLE ONLY normalized.icon_weather_mask
    ADD CONSTRAINT icon_weather_mask_icon_cell_fk FOREIGN KEY (source_grid_id, cell_index) REFERENCES normalized.icon_cell(source_grid_id, cell_index) ON DELETE CASCADE;


--
-- Name: hostrada_month_source hostrada_month_source_source_grid_id_fkey; Type: FK CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.hostrada_month_source
    ADD CONSTRAINT hostrada_month_source_source_grid_id_fkey FOREIGN KEY (source_grid_id) REFERENCES normalized.hostrada_grid(source_grid_id);


--
-- Name: icon_d2_ruc_field icon_d2_ruc_field_source_fk; Type: FK CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_d2_ruc_field
    ADD CONSTRAINT icon_d2_ruc_field_source_fk FOREIGN KEY (run_time_utc, lead_time, indicator) REFERENCES raw.icon_d2_ruc_source(run_time_utc, lead_time, indicator) ON DELETE CASCADE;


--
-- Name: icon_grid_cell_vertex icon_grid_cell_vertex_vertex_fk; Type: FK CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_grid_cell_vertex
    ADD CONSTRAINT icon_grid_cell_vertex_vertex_fk FOREIGN KEY (source_grid_id, vertex_index) REFERENCES raw.icon_grid_vertex(source_grid_id, vertex_index) ON DELETE CASCADE;


--
-- Name: icon_grid_vertex icon_grid_vertex_source_fk; Type: FK CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.icon_grid_vertex
    ADD CONSTRAINT icon_grid_vertex_source_fk FOREIGN KEY (source_grid_id) REFERENCES raw.icon_grid_source(source_grid_id) ON DELETE CASCADE;


--
-- The source-independent snapshot gate is part of the canonical operational
-- schema. It deliberately does not require the optional historical rebuild.
--

-- Validate an imported reference without consulting historical source files,
-- source manifests, HOSTRADA grid cells, or hourly weather observations.
CREATE OR REPLACE FUNCTION analytical.check_hostrada_reference_snapshot(
    p_geography_version TEXT,
    p_expected_plr_count INTEGER DEFAULT 542
)
RETURNS TABLE (
    passed BOOLEAN,
    expected_plr_count BIGINT,
    installed_plr_count BIGINT,
    expected_calendar_hour_count BIGINT,
    expected_observation_count BIGINT,
    plr_reference_count BIGINT,
    berlin_reference_count BIGINT,
    plr_sample_count_failure_count BIGINT,
    berlin_sample_count_failure_count BIGINT,
    unexpected_plr_geography_count BIGINT,
    unexpected_berlin_geography_count BIGINT,
    statistic_order_failure_count BIGINT
)
LANGUAGE sql
STABLE
AS $$
    WITH expected_hours AS MATERIALIZED (
        SELECT expected_hour.*
        FROM generate_series(1, 12) AS month(calendar_month)
        CROSS JOIN LATERAL analytical.hostrada_reference_expected_hours(
            month.calendar_month
        ) AS expected_hour
    ),
    expected_totals AS (
        SELECT
            COUNT(*)::BIGINT AS hour_count,
            COALESCE(SUM(expected_hour.sample_count), 0)::BIGINT
                AS observation_count
        FROM expected_hours AS expected_hour
    ),
    installed_geography AS (
        SELECT COUNT(*)::BIGINT AS plr_count
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ),
    plr_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
            )::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND expected_hour.sample_count IS DISTINCT FROM
                      reference_row.sample_count
            )::BIGINT AS sample_failure_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version <> p_geography_version
            )::BIGINT AS unexpected_geography_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND NOT (
                      reference_row.temperature_median_c
                          <= reference_row.temperature_p90_c
                      AND reference_row.temperature_p90_c
                          <= reference_row.temperature_max_c
                      AND reference_row.apparent_temperature_median_c
                          <= reference_row.apparent_temperature_p90_c
                      AND reference_row.apparent_temperature_p90_c
                          <= reference_row.apparent_temperature_max_c
                  )
            )::BIGINT AS statistic_failure_count
        FROM analytical.hostrada_plr_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
    ),
    berlin_quality AS (
        SELECT
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
            )::BIGINT AS row_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND expected_hour.sample_count IS DISTINCT FROM
                      reference_row.sample_count
            )::BIGINT AS sample_failure_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version <> p_geography_version
            )::BIGINT AS unexpected_geography_count,
            COUNT(*) FILTER (
                WHERE reference_row.geography_version = p_geography_version
                  AND NOT (
                      reference_row.temperature_median_c
                          <= reference_row.temperature_p90_c
                      AND reference_row.temperature_p90_c
                          <= reference_row.temperature_max_c
                      AND reference_row.apparent_temperature_median_c
                          <= reference_row.apparent_temperature_p90_c
                      AND reference_row.apparent_temperature_p90_c
                          <= reference_row.apparent_temperature_max_c
                  )
            )::BIGINT AS statistic_failure_count
        FROM analytical.hostrada_berlin_hourly_reference AS reference_row
        LEFT JOIN expected_hours AS expected_hour
          ON expected_hour.calendar_month = reference_row.calendar_month
         AND expected_hour.calendar_day = reference_row.calendar_day
         AND expected_hour.local_hour = reference_row.local_hour
    )
    SELECT
        p_geography_version IS NOT NULL
            AND p_expected_plr_count > 0
            AND geography.plr_count = p_expected_plr_count
            AND expected.hour_count = 8760
            AND expected.observation_count = 271559
            AND plr.row_count = expected.hour_count * p_expected_plr_count
            AND berlin.row_count = expected.hour_count
            AND plr.sample_failure_count = 0
            AND berlin.sample_failure_count = 0
            AND plr.unexpected_geography_count = 0
            AND berlin.unexpected_geography_count = 0
            AND plr.statistic_failure_count = 0
            AND berlin.statistic_failure_count = 0,
        p_expected_plr_count::BIGINT,
        geography.plr_count,
        expected.hour_count,
        expected.observation_count,
        plr.row_count,
        berlin.row_count,
        plr.sample_failure_count,
        berlin.sample_failure_count,
        plr.unexpected_geography_count,
        berlin.unexpected_geography_count,
        plr.statistic_failure_count + berlin.statistic_failure_count
    FROM expected_totals AS expected
    CROSS JOIN installed_geography AS geography
    CROSS JOIN plr_quality AS plr
    CROSS JOIN berlin_quality AS berlin;
$$;


--
-- PostgreSQL database dump complete
--
