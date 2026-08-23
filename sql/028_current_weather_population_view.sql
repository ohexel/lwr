CREATE OR REPLACE VIEW analytical.current_plr_weather_population AS
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
