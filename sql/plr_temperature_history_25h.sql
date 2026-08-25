-- Optional historical-year plotting data for the current 25-point forecast.
-- Ordinary operation still requires only the compact HOSTRADA reference.

CREATE TABLE IF NOT EXISTS analytical.plr_temperature_history_25h (
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


CREATE OR REPLACE FUNCTION analytical.refresh_plr_temperature_history_25h(
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


CREATE OR REPLACE VIEW analytical.current_plr_temperature_history_25h AS
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
