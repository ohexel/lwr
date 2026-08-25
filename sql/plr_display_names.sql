-- Add analyst-facing PLR labels to an existing installation without changing
-- forecast, population, historical-reference, or spatial-pipeline data.

CREATE TABLE IF NOT EXISTS analytical.plr_display_name (
    plr_id text NOT NULL,
    geography_version text NOT NULL,
    plr_name text NOT NULL,
    CONSTRAINT plr_display_name_plr_id_check
        CHECK (plr_id ~ '^[0-9]{8}$'),
    CONSTRAINT plr_display_name_plr_name_check
        CHECK (btrim(plr_name) <> ''),
    CONSTRAINT plr_display_name_pkey
        PRIMARY KEY (plr_id, geography_version),
    CONSTRAINT plr_display_name_plr_fkey
        FOREIGN KEY (plr_id, geography_version)
        REFERENCES normalized.plr (plr_id, geography_version)
);

DROP VIEW IF EXISTS analytical.current_plr_weather_context;
DROP VIEW IF EXISTS analytical.plr_weather_context;

CREATE VIEW analytical.plr_weather_context AS
WITH local_forecasts AS (
    SELECT
        forecast.*,
        forecast.valid_time_utc AT TIME ZONE 'Europe/Berlin'
            AS valid_time_berlin
    FROM analytical.plr_weather_population AS forecast
)
SELECT
    forecast.plr_id,
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
    plr_reference.apparent_temperature_median_c
        AS plr_apparent_temperature_median_c,
    plr_reference.apparent_temperature_p90_c
        AS plr_apparent_temperature_p90_c,
    plr_reference.apparent_temperature_max_c
        AS plr_apparent_temperature_max_c,
    berlin_reference.temperature_median_c AS berlin_temperature_median_c,
    berlin_reference.temperature_p90_c AS berlin_temperature_p90_c,
    berlin_reference.temperature_max_c AS berlin_temperature_max_c,
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
LEFT JOIN analytical.plr_display_name AS display_name
    ON display_name.plr_id = forecast.plr_id
   AND display_name.geography_version = forecast.geography_version
LEFT JOIN analytical.hostrada_plr_hourly_reference AS plr_reference
    ON plr_reference.calendar_month =
           EXTRACT(MONTH FROM forecast.valid_time_berlin)::smallint
   AND plr_reference.geography_version = forecast.geography_version
   AND plr_reference.plr_id = forecast.plr_id
   AND plr_reference.calendar_day =
           EXTRACT(DAY FROM forecast.valid_time_berlin)::smallint
   AND plr_reference.local_hour =
           EXTRACT(HOUR FROM forecast.valid_time_berlin)::smallint
LEFT JOIN analytical.hostrada_berlin_hourly_reference AS berlin_reference
    ON berlin_reference.calendar_month =
           EXTRACT(MONTH FROM forecast.valid_time_berlin)::smallint
   AND berlin_reference.geography_version = forecast.geography_version
   AND berlin_reference.calendar_day =
           EXTRACT(DAY FROM forecast.valid_time_berlin)::smallint
   AND berlin_reference.local_hour =
           EXTRACT(HOUR FROM forecast.valid_time_berlin)::smallint;

CREATE VIEW analytical.current_plr_weather_context AS
SELECT
    context_row.plr_id,
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
FROM analytical.plr_weather_context AS context_row
JOIN (
    SELECT current_row.run_time_utc, current_row.lead_time
    FROM analytical.current_plr_weather_population AS current_row
    LIMIT 1
) AS current_partition
    ON current_partition.run_time_utc = context_row.run_time_utc
   AND current_partition.lead_time = context_row.lead_time;
