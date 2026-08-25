-- Analyst-facing 25-point temperature trajectories and compact PLR summaries.
-- These views consume only existing forecast/population facts and the already
-- installed historical-reference medians. They never read HOSTRADA hourly data.

CREATE OR REPLACE VIEW analytical.current_plr_temperature_forecast_25h AS
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


CREATE OR REPLACE VIEW analytical.current_plr_temperature_summary_25h AS
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
