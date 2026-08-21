CREATE TABLE IF NOT EXISTS normalized.plr (
    plr_id TEXT,
    geometry geometry(MultiPolygon, 25833) NOT NULL,
    geography_version TEXT NOT NULL,
    reference_date DATE,
    PRIMARY KEY (plr_id, geography_version)
);

CREATE TABLE IF NOT EXISTS normalized.icon_cell (
    source_grid_id TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    geometry geometry(Polygon, 25833) NOT NULL,
    icon_cell_area_m2 DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        source_grid_id,
        cell_index
    ),
    CHECK (icon_cell_area_m2 > 0)
);

CREATE TABLE IF NOT EXISTS normalized.icon_plr_area_bridge (
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    intersection_area_m2 DOUBLE PRECISION NOT NULL,
    plr_area_m2 DOUBLE PRECISION NOT NULL,
    icon_cell_area_m2 DOUBLE PRECISION NOT NULL,
    fraction_of_plr DOUBLE PRECISION NOT NULL,
    fraction_of_icon_cell DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (
        plr_id,	
	geography_version,
        source_grid_id,
        cell_index
    ),
    FOREIGN KEY (
	plr_id,
	geography_version
    )
    REFERENCES normalized.plr (
	plr_id,
	geography_version
    ),
    FOREIGN KEY (
        source_grid_id,
        cell_index
    )
    REFERENCES normalized.icon_cell (
        source_grid_id,
        cell_index
    ),
    CHECK (intersection_area_m2 > 0),
    CHECK (plr_area_m2 > 0),
    CHECK (icon_cell_area_m2 > 0),
    CHECK (fraction_of_plr > 0),
    CHECK (fraction_of_icon_cell > 0)
);

CREATE TABLE IF NOT EXISTS normalized.plr_population_65plus (
    plr_id TEXT,
    population_total BIGINT NOT NULL,
    population_65_79 BIGINT NOT NULL,
    population_80plus BIGINT NOT NULL,
    population_65plus BIGINT NOT NULL,
    share_65plus DOUBLE PRECISION NOT NULL,
    reference_date DATE NOT NULL,
    publication_date DATE,
    PRIMARY KEY (plr_id, reference_date),
    CHECK (population_total >= 0),
    CHECK (population_65_79 >= 0),
    CHECK (population_80plus >= 0),
    CHECK (population_65plus >= 0),
    CHECK (population_65plus <= population_total),
    CHECK (share_65plus >= 0 AND share_65plus <= 1)
);

CREATE TABLE IF NOT EXISTS normalized.plr_population_rejected (
    plr_id TEXT NOT NULL,
    population_total BIGINT,
    population_65_79 BIGINT,
    population_80plus BIGINT,
    population_65plus BIGINT,
    share_65plus DOUBLE PRECISION,
    rejection_reason TEXT NOT NULL,
    reference_date DATE NOT NULL,
    publication_date DATE,
    rejected_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        plr_id,
        reference_date
    )
);

CREATE TABLE IF NOT EXISTS normalized.weather_partition_rejected (
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    observed_indicators JSONB,
    observed_row_counts JSONB,
    rejected_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        run_time_utc,
        lead_time
    )
);

CREATE TABLE IF NOT EXISTS normalized.icon_d2_ruc_weather (
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    cell_index INTEGER NOT NULL,
    temperature_c DOUBLE PRECISION,
    relative_humidity_percent DOUBLE PRECISION,
    dew_point_temperature_c DOUBLE PRECISION,
    wind_u_10m_ms DOUBLE PRECISION,
    wind_v_10m_ms DOUBLE PRECISION,
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        cell_index
    )
);
