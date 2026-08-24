BEGIN;

-- Internal geography joins prevent mixing PLR boundary versions. Analysts
-- receive the agreed lean contract without operational metadata or derived
-- interpretations.
CREATE OR REPLACE VIEW analytical.plr_weather_context AS
WITH local_forecasts AS (
    SELECT
        weather_row.*,
        weather_row.valid_time_utc
            AT TIME ZONE 'Europe/Berlin' AS valid_time_berlin
    FROM analytical.plr_weather_population AS weather_row
)
SELECT
    forecast.plr_id,
    forecast.run_time_utc,
    forecast.lead_time,
    forecast.valid_time_utc,
    forecast.valid_time_berlin,
    forecast.temperature_c,
    forecast.apparent_temperature_shade_c,
    plr_reference.temperature_median_c
        AS plr_temperature_median_c,
    plr_reference.temperature_p90_c
        AS plr_temperature_p90_c,
    plr_reference.temperature_max_c
        AS plr_temperature_max_c,
    plr_reference.apparent_temperature_median_c
        AS plr_apparent_temperature_median_c,
    plr_reference.apparent_temperature_p90_c
        AS plr_apparent_temperature_p90_c,
    plr_reference.apparent_temperature_max_c
        AS plr_apparent_temperature_max_c,
    berlin_reference.temperature_median_c
        AS berlin_temperature_median_c,
    berlin_reference.temperature_p90_c
        AS berlin_temperature_p90_c,
    berlin_reference.temperature_max_c
        AS berlin_temperature_max_c,
    berlin_reference.apparent_temperature_median_c
        AS berlin_apparent_temperature_median_c,
    berlin_reference.apparent_temperature_p90_c
        AS berlin_apparent_temperature_p90_c,
    berlin_reference.apparent_temperature_max_c
        AS berlin_apparent_temperature_max_c,
    forecast.population_total,
    forecast.population_65plus,
    forecast.population_status
FROM local_forecasts AS forecast
LEFT JOIN analytical.hostrada_plr_hourly_reference AS plr_reference
  ON plr_reference.calendar_month
        = EXTRACT(MONTH FROM forecast.valid_time_berlin)::SMALLINT
 AND plr_reference.geography_version = forecast.geography_version
 AND plr_reference.plr_id = forecast.plr_id
 AND plr_reference.calendar_day
        = EXTRACT(DAY FROM forecast.valid_time_berlin)::SMALLINT
 AND plr_reference.local_hour
        = EXTRACT(HOUR FROM forecast.valid_time_berlin)::SMALLINT
LEFT JOIN analytical.hostrada_berlin_hourly_reference AS berlin_reference
  ON berlin_reference.calendar_month
        = EXTRACT(MONTH FROM forecast.valid_time_berlin)::SMALLINT
 AND berlin_reference.geography_version = forecast.geography_version
 AND berlin_reference.calendar_day
        = EXTRACT(DAY FROM forecast.valid_time_berlin)::SMALLINT
 AND berlin_reference.local_hour
        = EXTRACT(HOUR FROM forecast.valid_time_berlin)::SMALLINT;


-- Preserve the existing latest-run/earliest-valid-time partition semantics.
CREATE OR REPLACE VIEW analytical.current_plr_weather_context AS
SELECT context_row.*
FROM analytical.plr_weather_context AS context_row
JOIN (
    SELECT
        current_row.run_time_utc,
        current_row.lead_time
    FROM analytical.current_plr_weather_population AS current_row
    LIMIT 1
) AS current_partition
  ON current_partition.run_time_utc = context_row.run_time_utc
 AND current_partition.lead_time = context_row.lead_time;

COMMIT;
