CREATE TABLE IF NOT EXISTS analytical.plr_weather (
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    relative_humidity_percent DOUBLE PRECISION,
    dew_point_temperature_c DOUBLE PRECISION,
    wind_u_10m_ms DOUBLE PRECISION,
    wind_v_10m_ms DOUBLE PRECISION,
    wind_speed_10m_ms DOUBLE PRECISION,
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
	geography_version
    ),
    FOREIGN KEY (plr_id, geography_version)
        REFERENCES normalized.plr (plr_id, geography_version)
);

CREATE TABLE IF NOT EXISTS analytical.plr_weather_population (
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    relative_humidity_percent DOUBLE PRECISION,
    dew_point_temperature_c DOUBLE PRECISION,
    wind_u_10m_ms DOUBLE PRECISION,
    wind_v_10m_ms DOUBLE PRECISION,
    wind_speed_10m_ms DOUBLE PRECISION,
    population_total BIGINT,
    population_65plus BIGINT,
    share_65plus DOUBLE PRECISION,
    population_status TEXT NOT NULL,
    population_rejection_reason TEXT,
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
	geography_version
    ),
    FOREIGN KEY (plr_id, geography_version)
        REFERENCES normalized.plr (plr_id, geography_version),
    CHECK (
        population_status IN (
            'available',
            'rejected_source_record'
        )
    )
);

CREATE TABLE IF NOT EXISTS analytical.plr_weather_population_rejected (
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    plr_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    rejected_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
	geography_version
    )
);
