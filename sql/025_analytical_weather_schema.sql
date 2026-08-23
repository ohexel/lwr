BEGIN;

TRUNCATE TABLE
    analytical.plr_weather_population,
    analytical.plr_weather_population_rejected,
    analytical.plr_weather;

ALTER TABLE analytical.plr_weather
    ADD COLUMN IF NOT EXISTS source_grid_id TEXT;

ALTER TABLE analytical.plr_weather
    ADD COLUMN IF NOT EXISTS geography_version TEXT;

ALTER TABLE analytical.plr_weather
    ALTER COLUMN source_grid_id SET NOT NULL;

ALTER TABLE analytical.plr_weather
    ALTER COLUMN geography_version SET NOT NULL;

ALTER TABLE analytical.plr_weather
    DROP CONSTRAINT IF EXISTS plr_weather_pkey;

ALTER TABLE analytical.plr_weather
    DROP CONSTRAINT IF EXISTS plr_weather_plr_id_fkey;

ALTER TABLE analytical.plr_weather
    DROP CONSTRAINT IF EXISTS plr_weather_plr_id_geography_version_fkey;

ALTER TABLE analytical.plr_weather
    DROP CONSTRAINT IF EXISTS plr_weather_plr_fk;

ALTER TABLE analytical.plr_weather
    ADD CONSTRAINT plr_weather_pkey
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
        geography_version
    );

ALTER TABLE analytical.plr_weather
    ADD CONSTRAINT plr_weather_plr_fk
    FOREIGN KEY (
        plr_id,
        geography_version
    )
    REFERENCES normalized.plr (
        plr_id,
        geography_version
    );

ALTER TABLE analytical.plr_weather_population
    ADD COLUMN IF NOT EXISTS source_grid_id TEXT;

ALTER TABLE analytical.plr_weather_population
    ADD COLUMN IF NOT EXISTS geography_version TEXT;

ALTER TABLE analytical.plr_weather_population
    ADD COLUMN IF NOT EXISTS population_reference_date DATE;

ALTER TABLE analytical.plr_weather_population
    ADD COLUMN IF NOT EXISTS population_publication_date DATE;

ALTER TABLE analytical.plr_weather_population
    ADD COLUMN IF NOT EXISTS population_source_sha256 TEXT;

ALTER TABLE analytical.plr_weather_population
    ALTER COLUMN source_grid_id SET NOT NULL;

ALTER TABLE analytical.plr_weather_population
    ALTER COLUMN geography_version SET NOT NULL;

ALTER TABLE analytical.plr_weather_population
    ALTER COLUMN population_reference_date SET NOT NULL;

ALTER TABLE analytical.plr_weather_population
    ALTER COLUMN population_source_sha256 SET NOT NULL;

ALTER TABLE analytical.plr_weather_population
    DROP CONSTRAINT IF EXISTS plr_weather_population_pkey;

ALTER TABLE analytical.plr_weather_population
    DROP CONSTRAINT IF EXISTS plr_weather_population_plr_id_fkey;

ALTER TABLE analytical.plr_weather_population
    DROP CONSTRAINT IF EXISTS plr_weather_population_plr_fk;

ALTER TABLE analytical.plr_weather_population
    DROP CONSTRAINT IF EXISTS plr_weather_population_weather_fk;

ALTER TABLE analytical.plr_weather_population
    DROP CONSTRAINT IF EXISTS plr_weather_population_population_status_check;

ALTER TABLE analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_pkey
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
        geography_version
    );

ALTER TABLE analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_plr_fk
    FOREIGN KEY (
        plr_id,
        geography_version
    )
    REFERENCES normalized.plr (
        plr_id,
        geography_version
    );

ALTER TABLE analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_weather_fk
    FOREIGN KEY (
        run_time_utc,
        lead_time,
        plr_id,
        geography_version
    )
    REFERENCES analytical.plr_weather (
        run_time_utc,
        lead_time,
        plr_id,
        geography_version
    )
    ON DELETE CASCADE;

ALTER TABLE analytical.plr_weather_population
    ADD CONSTRAINT plr_weather_population_population_status_check
    CHECK (
        population_status IN (
            'available',
            'rejected_source_record'
        )
    );

ALTER TABLE analytical.plr_weather_population_rejected
    ADD COLUMN IF NOT EXISTS geography_version TEXT;

ALTER TABLE analytical.plr_weather_population_rejected
    ADD COLUMN IF NOT EXISTS population_reference_date DATE;

ALTER TABLE analytical.plr_weather_population_rejected
    ADD COLUMN IF NOT EXISTS rejection_details JSONB;

ALTER TABLE analytical.plr_weather_population_rejected
    ALTER COLUMN geography_version SET NOT NULL;

ALTER TABLE analytical.plr_weather_population_rejected
    DROP CONSTRAINT IF EXISTS plr_weather_population_rejected_pkey;

ALTER TABLE analytical.plr_weather_population_rejected
    ADD CONSTRAINT plr_weather_population_rejected_pkey
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        plr_id,
        geography_version
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

COMMIT;
