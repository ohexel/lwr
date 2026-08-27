-- Add apparent temperature to an already materialized 25-hour history cache.
-- The source lookup uses the HOSTRADA primary-key order.

ALTER TABLE analytical.plr_temperature_history_25h
    ADD COLUMN historical_apparent_temperature_c double precision;

UPDATE analytical.plr_temperature_history_25h AS history
SET historical_apparent_temperature_c = (
    SELECT source.apparent_temperature_shade_c
    FROM analytical.hostrada_plr_hourly AS source
    WHERE source.source_month_utc = date_trunc(
            'month',
            history.historical_valid_time_utc AT TIME ZONE 'UTC'
          )::date
      AND source.valid_time_utc = history.historical_valid_time_utc
      AND source.plr_id = history.plr_id
    -- Preserve a parameterized primary-key lookup per retained history row.
    OFFSET 0
);

DO $validation$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM analytical.plr_temperature_history_25h
        WHERE historical_apparent_temperature_c IS NULL
    ) THEN
        RAISE EXCEPTION
            'Existing historical trajectories cannot be extended because matching HOSTRADA apparent-temperature observations are unavailable.';
    END IF;
END;
$validation$;

ALTER TABLE analytical.plr_temperature_history_25h
    ALTER COLUMN historical_apparent_temperature_c SET NOT NULL;
