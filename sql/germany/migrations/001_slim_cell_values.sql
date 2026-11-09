-- One-off migration (decision B, 2026-11-09): replace the wide
-- normalized.de_icon_cell_weather (≈180 B/row, 1,259 MB for 2 runs) with
-- normalized.de_forecast_run + normalized.de_icon_cell_value (361 MB measured
-- on a copy of the same 2 runs). Values are copied unchanged (double precision).
--
-- Germany-only objects; nothing Berlin is touched. Run once, after
-- sql/germany/forecast_schema.sql has created the new tables:
--   psql -1 -v ON_ERROR_STOP=1 -f sql/germany/migrations/001_slim_cell_values.sql
-- Safe to re-run: does nothing once the old table is gone.

DO $$
BEGIN
    IF to_regclass('normalized.de_icon_cell_weather') IS NULL THEN
        RAISE NOTICE 'normalized.de_icon_cell_weather already removed; nothing to do';
        RETURN;
    END IF;

    INSERT INTO normalized.de_forecast_run (run_time_utc, geography_version, source_grid_id)
    SELECT DISTINCT run_time_utc, geography_version, source_grid_id
    FROM normalized.de_icon_cell_weather
    ON CONFLICT (run_time_utc) DO NOTHING;

    INSERT INTO normalized.de_icon_cell_value
        (run_time_utc, lead_hour, cell_index, temperature_c, apparent_temperature_shade_c)
    SELECT
        run_time_utc,
        (EXTRACT(EPOCH FROM (valid_time_utc - run_time_utc)) / 3600)::smallint,
        cell_index,
        temperature_c,
        apparent_temperature_shade_c
    FROM normalized.de_icon_cell_weather
    ORDER BY 1, 2, 3
    ON CONFLICT DO NOTHING;

    IF (SELECT COUNT(*) FROM normalized.de_icon_cell_weather)
       <> (SELECT COUNT(*) FROM normalized.de_icon_cell_value AS v
           WHERE v.run_time_utc IN (
               SELECT DISTINCT run_time_utc FROM normalized.de_icon_cell_weather)) THEN
        RAISE EXCEPTION 'row counts differ after copy; aborting';
    END IF;

    DROP TABLE normalized.de_icon_cell_weather;
END;
$$;
