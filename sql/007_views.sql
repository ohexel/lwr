CREATE OR REPLACE VIEW analytical.current_plr_weather_population AS
WITH latest_partition AS (
    SELECT
        run_time_utc,
        lead_time
    FROM analytical.plr_weather_population
    GROUP BY
        run_time_utc,
        lead_time
    ORDER BY
        run_time_utc DESC,
        lead_time ASC
    LIMIT 1
)
SELECT a.*
FROM analytical.plr_weather_population AS a
JOIN latest_partition AS p
  ON a.run_time_utc = p.run_time_utc
 AND a.lead_time = p.lead_time;
