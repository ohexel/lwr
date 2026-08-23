BEGIN;

-- Canonical weather normalization in PostgreSQL.
-- Existing rows are reconstructible from retained raw weather and are
-- intentionally cleared before the table identity is tightened.

TRUNCATE TABLE
    normalized.icon_d2_ruc_weather,
    normalized.weather_partition_rejected;

ALTER TABLE normalized.icon_d2_ruc_weather
    ADD COLUMN IF NOT EXISTS source_grid_id TEXT;

ALTER TABLE normalized.icon_d2_ruc_weather
    ADD COLUMN IF NOT EXISTS geography_version TEXT;

ALTER TABLE normalized.icon_d2_ruc_weather
    ADD COLUMN IF NOT EXISTS mask_buffer_m INTEGER;

ALTER TABLE normalized.icon_d2_ruc_weather
    ALTER COLUMN source_grid_id SET NOT NULL;

ALTER TABLE normalized.icon_d2_ruc_weather
    ALTER COLUMN geography_version SET NOT NULL;

ALTER TABLE normalized.icon_d2_ruc_weather
    ALTER COLUMN mask_buffer_m SET NOT NULL;

ALTER TABLE normalized.icon_d2_ruc_weather
    DROP CONSTRAINT IF EXISTS icon_d2_ruc_weather_pkey;

ALTER TABLE normalized.icon_d2_ruc_weather
    DROP CONSTRAINT IF EXISTS icon_d2_ruc_weather_icon_cell_fk;

ALTER TABLE normalized.icon_d2_ruc_weather
    ADD CONSTRAINT icon_d2_ruc_weather_pkey
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        source_grid_id,
        geography_version,
        cell_index
    );

ALTER TABLE normalized.icon_d2_ruc_weather
    ADD CONSTRAINT icon_d2_ruc_weather_icon_cell_fk
    FOREIGN KEY (
        source_grid_id,
        cell_index
    )
    REFERENCES normalized.icon_cell (
        source_grid_id,
        cell_index
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'icon_d2_ruc_weather_mask_buffer_nonnegative'
    ) THEN
        ALTER TABLE normalized.icon_d2_ruc_weather
            ADD CONSTRAINT icon_d2_ruc_weather_mask_buffer_nonnegative
            CHECK (mask_buffer_m >= 0);
    END IF;
END
$$;

ALTER TABLE normalized.weather_partition_rejected
    ADD COLUMN IF NOT EXISTS source_grid_id TEXT;

ALTER TABLE normalized.weather_partition_rejected
    ADD COLUMN IF NOT EXISTS geography_version TEXT;

ALTER TABLE normalized.weather_partition_rejected
    ADD COLUMN IF NOT EXISTS mask_buffer_m INTEGER;

ALTER TABLE normalized.weather_partition_rejected
    ADD COLUMN IF NOT EXISTS rejection_details JSONB;

CREATE INDEX IF NOT EXISTS idx_normalized_weather_partition
    ON normalized.icon_d2_ruc_weather (
        run_time_utc,
        lead_time
    );

CREATE INDEX IF NOT EXISTS idx_normalized_weather_scope
    ON normalized.icon_d2_ruc_weather (
        source_grid_id,
        geography_version,
        cell_index
    );

COMMIT;
