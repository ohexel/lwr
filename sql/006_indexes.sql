CREATE INDEX IF NOT EXISTS idx_normalized_plr_geometry
    ON normalized.plr
    USING GIST (geometry);

CREATE INDEX IF NOT EXISTS idx_normalized_icon_cell_geometry
    ON normalized.icon_cell
    USING GIST (geometry);

CREATE INDEX IF NOT EXISTS idx_icon_grid_cell_vertex_vertex
    ON raw.icon_grid_cell_vertex (
        source_grid_id,
        vertex_index
    );

CREATE INDEX IF NOT EXISTS idx_raw_weather_partition
    ON raw.icon_d2_ruc_field (
        run_time_utc,
        lead_time,
        indicator
    );

CREATE INDEX IF NOT EXISTS idx_normalized_weather_partition
    ON normalized.icon_d2_ruc_weather (
        run_time_utc,
        lead_time
    );

CREATE INDEX IF NOT EXISTS idx_analytical_plr_weather_partition
    ON analytical.plr_weather (
        run_time_utc,
        lead_time
    );

CREATE INDEX IF NOT EXISTS idx_analytical_weather_population_partition
    ON analytical.plr_weather_population (
        run_time_utc,
        lead_time
    );
