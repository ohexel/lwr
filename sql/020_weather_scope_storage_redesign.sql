BEGIN;

-- Permanent weather storage is a Berlin-scoped, source-faithful projection.
-- The retained GRIB remains the complete source.

DROP FUNCTION IF EXISTS raw.check_icon_d2_ruc_field_partition(
    TIMESTAMPTZ,
    TEXT,
    TIMESTAMPTZ,
    INTEGER
);

CREATE TABLE IF NOT EXISTS normalized.icon_weather_mask (
    geography_version TEXT NOT NULL,
    source_grid_id TEXT NOT NULL,
    mask_buffer_m INTEGER NOT NULL,
    cell_index INTEGER NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        geography_version,
        source_grid_id,
        mask_buffer_m,
        cell_index
    ),
    CHECK (mask_buffer_m >= 0)
);

ALTER TABLE normalized.icon_weather_mask
    DROP CONSTRAINT IF EXISTS icon_weather_mask_icon_cell_fk;

ALTER TABLE normalized.icon_weather_mask
    ADD CONSTRAINT icon_weather_mask_icon_cell_fk
    FOREIGN KEY (
        source_grid_id,
        cell_index
    )
    REFERENCES normalized.icon_cell (
        source_grid_id,
        cell_index
    )
    ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_icon_weather_mask_cell
    ON normalized.icon_weather_mask (
        source_grid_id,
        cell_index
    );

CREATE TABLE IF NOT EXISTS raw.icon_d2_ruc_source (
    run_time_utc TIMESTAMPTZ NOT NULL,
    lead_time TEXT NOT NULL,
    indicator TEXT NOT NULL,
    valid_time_utc TIMESTAMPTZ NOT NULL,
    source_grid_id TEXT NOT NULL,
    geography_version TEXT NOT NULL,
    mask_buffer_m INTEGER NOT NULL,
    source_unit TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_point_count INTEGER NOT NULL,
    source_missing_value_count INTEGER NOT NULL,
    retained_point_count INTEGER NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        run_time_utc,
        lead_time,
        indicator
    ),
    CHECK (source_point_count > 0),
    CHECK (source_missing_value_count >= 0),
    CHECK (retained_point_count > 0),
    CHECK (retained_point_count <= source_point_count),
    CHECK (mask_buffer_m >= 0)
);

-- TRUNCATE releases the old relation pages immediately. DELETE would leave
-- them allocated until a table rewrite such as VACUUM FULL.
TRUNCATE TABLE raw.icon_d2_ruc_field;

ALTER TABLE raw.icon_d2_ruc_field
    DROP COLUMN IF EXISTS valid_time_utc,
    DROP COLUMN IF EXISTS source_unit,
    DROP COLUMN IF EXISTS source_url,
    DROP COLUMN IF EXISTS raw_path,
    DROP COLUMN IF EXISTS source_sha256,
    DROP COLUMN IF EXISTS loaded_at_utc;

-- Redundant with the primary-key prefix once the retained table is small.
DROP INDEX IF EXISTS raw.idx_raw_weather_partition;

ALTER TABLE raw.icon_d2_ruc_field
    DROP CONSTRAINT IF EXISTS icon_d2_ruc_field_source_fk;

ALTER TABLE raw.icon_d2_ruc_field
    ADD CONSTRAINT icon_d2_ruc_field_source_fk
    FOREIGN KEY (
        run_time_utc,
        lead_time,
        indicator
    )
    REFERENCES raw.icon_d2_ruc_source (
        run_time_utc,
        lead_time,
        indicator
    )
    ON DELETE CASCADE;

CREATE OR REPLACE FUNCTION normalized.refresh_icon_weather_mask(
    p_geography_version TEXT,
    p_source_grid_id TEXT,
    p_mask_buffer_m INTEGER DEFAULT 5000
)
RETURNS TABLE (
    mask_cell_count BIGINT,
    bridge_cell_count BIGINT,
    missing_bridge_cell_count BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_mask_buffer_m < 0 THEN
        RAISE EXCEPTION 'mask buffer must be non-negative';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    ) THEN
        RAISE EXCEPTION
            'No PLRs found for geography_version=%',
            p_geography_version;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM normalized.icon_cell AS icon_row
        WHERE icon_row.source_grid_id = p_source_grid_id
    ) THEN
        RAISE EXCEPTION
            'No ICON cells found for source_grid_id=%',
            p_source_grid_id;
    END IF;

    DELETE FROM normalized.icon_weather_mask AS mask_row
    WHERE mask_row.geography_version = p_geography_version
      AND mask_row.source_grid_id = p_source_grid_id
      AND mask_row.mask_buffer_m = p_mask_buffer_m;

    INSERT INTO normalized.icon_weather_mask (
        geography_version,
        source_grid_id,
        mask_buffer_m,
        cell_index
    )
    WITH berlin AS (
        SELECT
            ST_Buffer(
                ST_UnaryUnion(ST_Collect(plr_row.geometry)),
                p_mask_buffer_m
            ) AS geometry
        FROM normalized.plr AS plr_row
        WHERE plr_row.geography_version = p_geography_version
    )
    SELECT
        p_geography_version,
        icon_row.source_grid_id,
        p_mask_buffer_m,
        icon_row.cell_index
    FROM normalized.icon_cell AS icon_row
    CROSS JOIN berlin
    WHERE icon_row.source_grid_id = p_source_grid_id
      AND icon_row.geometry && berlin.geometry
      AND ST_Intersects(icon_row.geometry, berlin.geometry);

    RETURN QUERY
    WITH bridge_cells AS (
        SELECT DISTINCT bridge_row.cell_index
        FROM normalized.icon_plr_area_bridge AS bridge_row
        WHERE bridge_row.geography_version = p_geography_version
          AND bridge_row.source_grid_id = p_source_grid_id
    ),
    mask_cells AS (
        SELECT mask_row.cell_index
        FROM normalized.icon_weather_mask AS mask_row
        WHERE mask_row.geography_version = p_geography_version
          AND mask_row.source_grid_id = p_source_grid_id
          AND mask_row.mask_buffer_m = p_mask_buffer_m
    )
    SELECT
        (SELECT COUNT(*) FROM mask_cells)::BIGINT,
        (SELECT COUNT(*) FROM bridge_cells)::BIGINT,
        (
            SELECT COUNT(*)
            FROM bridge_cells AS bridge_cell
            WHERE NOT EXISTS (
                SELECT 1
                FROM mask_cells AS mask_cell
                WHERE mask_cell.cell_index = bridge_cell.cell_index
            )
        )::BIGINT;
END;
$$;

COMMIT;
